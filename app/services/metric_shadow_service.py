"""Explicit backend-only Metric shadow seam. Existing consumer routes stay Pandas."""
from copy import deepcopy
from dataclasses import dataclass
import os
import time
import logging

from app.db.database import repository_transaction
from app.db.query_governance_repository import ReservationConflict
from app.processing.metric_execution import (
    MAX_GROUPS, PANDAS_ENGINE_ID, PANDAS_ENGINE_VERSION, MetricExecutionPlan,
    PandasMetricExecutionEngine,
)
from app.processing.metric_shadow import (
    MetricShadowComparison, ShadowOutcome, compare_metric_results, check_numeric_portability,
)
from app.processing.metric_warehouse_compiler import (
    COMPILER_VERSION, compile_metric_query, decode_metric_query_result,
)
from app.services.governed_query_service import (
    GovernedExecutionState, QueryRecoveryRequired, RecoveredQueryFailed,
    execute_governed_query, recover_governed_query,
)
from app.services.metric_warehouse_binding_service import resolve_metric_warehouse_sources
from app.services.query_governance_service import QueryGovernanceService
from app.services.workspace_understanding_service import validate_workspace
from app.warehouse.providers.base import ProviderError

logger = logging.getLogger(__name__)


def shadow_enabled():
    return os.getenv('METRIC_BIGQUERY_SHADOW_ENABLED', 'false').lower() == 'true'


@dataclass(frozen=True)
class MetricShadowEvidence:
    outcome: ShadowOutcome
    plan_signature: str
    reason: str | None = None
    pandas_engine: str = PANDAS_ENGINE_ID
    pandas_version: int = PANDAS_ENGINE_VERSION
    compiler_version: int = COMPILER_VERSION
    consumer: str = 'METRIC'
    publication_pins: tuple = ()
    request_token: str | None = None
    query_intent: str | None = None
    reservation_id: int | None = None
    provider_job_id: str | None = None
    bytes_processed: int | None = None
    bytes_billed: int | None = None
    duration_ms: int = 0
    comparison: MetricShadowComparison | None = None


@dataclass(frozen=True)
class MetricShadowExecution:
    # Internal return contract; never added to any existing API response model.
    pandas_result: dict
    evidence: MetricShadowEvidence
    warehouse_result: dict | None = None


def _owned_sources(plan, user):
    validate_workspace(plan.workspace_id, user)
    with repository_transaction() as conn:
        return resolve_metric_warehouse_sources(conn, plan)


def _read_provider():
    # Lazy SDK/ADC construction, only after opt-in, ownership and compilation.
    from app.query_governance.providers.bigquery import BigQueryReadProvider
    from app.warehouse.providers.base import Policy
    return BigQueryReadProvider(Policy.from_environment())


class MetricShadowService:
    def __init__(self, workspace_id, user, *, source_resolver=None,
                 governance_factory=None, provider_factory=None):
        if type(workspace_id) is not int or workspace_id < 1:
            raise ValueError('Positive workspace required')
        if not isinstance(user,dict) or not isinstance(user.get('owner_key'),str) or not user['owner_key']:
            raise ValueError('Authenticated workspace principal required')
        self.workspace_id, self.user = workspace_id, user
        self.source_resolver = source_resolver or _owned_sources
        self.governance_factory = governance_factory or QueryGovernanceService
        self.provider_factory = provider_factory or _read_provider

    def execute(self, plan, frames):
        """Pandas succeeds/fails under its existing rules; shadow never replaces it."""
        pandas_result = PandasMetricExecutionEngine().execute(plan, frames)
        return self.compare(plan, pandas_result, frames=frames)

    def compare(self, plan: MetricExecutionPlan, pandas_result, *, frames=None,
                recovery: MetricShadowEvidence | None = None):
        started = time.monotonic()
        pins = dict(plan_signature=plan.signature if isinstance(plan,MetricExecutionPlan) else '')
        def finish(outcome, *, warehouse_result=None, **values):
            return MetricShadowExecution(pandas_result,
                MetricShadowEvidence(outcome, **(pins | values),
                    duration_ms=int((time.monotonic()-started)*1000)), warehouse_result)
        if not shadow_enabled():
            return finish(ShadowOutcome.DISABLED)
        if not isinstance(plan,MetricExecutionPlan) or plan.workspace_id != self.workspace_id:
            return finish(ShadowOutcome.INELIGIBLE,reason='WORKSPACE_MISMATCH')
        plan = deepcopy(plan)
        try:
            sources = self.source_resolver(plan,self.user)
            for source in sources.values():
                parts = source.physical_table.split('.')
                exact = (f'm{source.model_id}_v{source.model_version_id}_g{source.generation}'
                         f'_p{source.publication_id}_r{source.publication_revision}')
                if (parts[1:] != [f'dr_dev_w{plan.workspace_id}',exact] or source.schema_sha256 is None):
                    raise ValueError('WAREHOUSE_IMMUTABLE_BINDING_REQUIRED')
            if frames is not None:
                for node in plan.definition['formula']['nodes']:
                    if node['op'] == 'COLUMN':
                        ref=node['column']
                        if getattr(frames[ref['dataset_id']][ref['column']].dtype,'kind',None) == 'f':
                            raise ValueError('WAREHOUSE_FLOAT_UNSUPPORTED')
                ref=plan.definition.get('time_field')
                if ref:
                    source=sources[ref['dataset_id']]
                    dtype=frames[ref['dataset_id']][ref['column']].dtype
                    kind=next(t for n,_,t in source.columns if n==ref['column'])
                    if kind=='TIMESTAMP':
                        tz=getattr(dtype,'tz',None)
                        expected='UTC' if source.event_time_interpretation=='UTC' else source.event_time_timezone
                        if str(tz) != expected:
                            raise ValueError('TIME_SEMANTICS_UNRESOLVED')
                check_numeric_portability(plan,frames,
                    {(i,n):t for i,s in sources.items() for n,_,t in s.columns},pandas_result)
            query = compile_metric_query(plan,sources)
            publication_pins=tuple(tuple(sorted(sources[i].pin().items())) for i in sorted(sources))
            pins.update(publication_pins=publication_pins, request_token=query.request_token,
                        query_intent=query.intent_sha256)
        except Exception as error:
            allowed={'WAREHOUSE_FLOAT_UNSUPPORTED','TIME_SEMANTICS_UNRESOLVED','UNRESOLVED_FILTER',
                'WAREHOUSE_SERVING_NOT_READY','WAREHOUSE_IMMUTABLE_BINDING_REQUIRED','NUMERIC_PRECISION_UNSUPPORTED'}
            reason=str(error) if isinstance(error,ValueError) and str(error) in allowed else 'WAREHOUSE_INELIGIBLE'
            return finish(ShadowOutcome.INELIGIBLE,reason=reason,
                          reservation_id=recovery.reservation_id if recovery else None)
        if recovery is not None:
            if (recovery.outcome != ShadowOutcome.RECOVERY_REQUIRED or recovery.plan_signature != plan.signature
                    or recovery.request_token != query.request_token or recovery.query_intent != query.intent_sha256
                    or recovery.publication_pins != publication_pins):
                return finish(ShadowOutcome.REVALIDATION_REQUIRED,reason='QUERY_RECOVERY_SCOPE_MISMATCH',
                    reservation_id=recovery.reservation_id)
        try:
            governance=self.governance_factory(plan.workspace_id,self.user)
            provider=self.provider_factory()
            if recovery is None:
                observed=execute_governed_query(governance=governance,provider=provider,query=query,
                    consumer='METRIC',requested_rows=MAX_GROUPS+1)
            else:
                observed=recover_governed_query(governance=governance,provider=provider,query=query,
                    consumer='METRIC',reservation_id=recovery.reservation_id)
            pins['reservation_id']=observed.reservation_id
        except QueryRecoveryRequired as error:
            return finish(ShadowOutcome.RECOVERY_REQUIRED,reason='QUERY_RECOVERY_REQUIRED',
                          reservation_id=error.reservation_id)
        except ReservationConflict:
            return finish(ShadowOutcome.REVALIDATION_REQUIRED,reason='GOVERNANCE_CONFLICT',
                          reservation_id=recovery.reservation_id if recovery else None)
        except RecoveredQueryFailed as error:
            return finish(ShadowOutcome.PROVIDER_ERROR,reason='QUERY_FAILED',reservation_id=error.reservation_id)
        except ProviderError as error:
            return finish(ShadowOutcome.PROVIDER_ERROR,reason=error.code.value)
        except Exception:
            return finish(ShadowOutcome.PROVIDER_ERROR,reason='SHADOW_PROVIDER_ERROR')
        if observed.state != GovernedExecutionState.SUCCEEDED:
            outcome=(ShadowOutcome.NOT_ADMITTED if observed.state == GovernedExecutionState.NOT_ADMITTED
                     else ShadowOutcome.REVALIDATION_REQUIRED)
            return finish(outcome,reason=outcome.value)
        result=observed.result
        pins.update(provider_job_id=result.provider_job_id,bytes_processed=result.bytes_processed,
                    bytes_billed=result.bytes_billed)
        try:
            decoded=decode_metric_query_result(plan,result,sources=sources)
        except Exception as error:
            allowed={'JOIN_COVERAGE','WAREHOUSE_RESULT_SHAPE','WAREHOUSE_RESULT_TYPE',
                     'WAREHOUSE_RESULT_TIME','WAREHOUSE_RESULT_TRUNCATED','OUTPUT_LIMIT'}
            reason=str(error) if isinstance(error,ValueError) and str(error) in allowed else 'SHADOW_DECODE_FAILED'
            return finish(ShadowOutcome.PROVIDER_ERROR,reason=reason)
        try:
            group_kinds=tuple(next(t for n,_,t in sources[r['dataset_id']].columns if n==r['column'])
                              for r in plan.definition['dimensions'])
            comparison=compare_metric_results(plan,pandas_result,decoded,query_intent=query.intent_sha256,
                compiler_version=query.compiler_version,publication_pins=publication_pins,group_kinds=group_kinds)
        except Exception:
            return finish(ShadowOutcome.PROVIDER_ERROR,reason='SHADOW_COMPARISON_FAILED')
        return finish(ShadowOutcome.MATCH if comparison.match else ShadowOutcome.MISMATCH,
                      comparison=comparison,warehouse_result=decoded)


def observe_metric_shadow(plan, pandas_result, user, *, frames=None, observer=None):
    """Post-success hook: only safe evidence escapes; no shadow error affects Pandas."""
    if not shadow_enabled():
        return None
    try:
        evidence=MetricShadowService(plan.workspace_id,user).compare(plan,pandas_result,frames=frames).evidence
    except Exception:
        evidence=MetricShadowEvidence(ShadowOutcome.PROVIDER_ERROR,plan.signature,reason='SHADOW_SERVICE_ERROR')
    if observer is not None:
        try:
            observer(evidence)
        except Exception:
            logger.warning('Metric shadow observer failed workspace=%s candidate=%s',
                           plan.workspace_id,plan.candidate_id)
    revisions=tuple((dict(pin)['publication_id'],dict(pin)['publication_revision'])
                    for pin in evidence.publication_pins)
    logger.info('Metric shadow workspace=%s candidate=%s outcome=%s reason=%s reservation=%s job=%s '
                'processed=%s billed=%s duration_ms=%s consumer=METRIC plan=%s publications=%s', plan.workspace_id,plan.candidate_id,
                evidence.outcome,evidence.reason,evidence.reservation_id,evidence.provider_job_id,
                evidence.bytes_processed,evidence.bytes_billed,evidence.duration_ms,evidence.plan_signature,revisions)
    return evidence
