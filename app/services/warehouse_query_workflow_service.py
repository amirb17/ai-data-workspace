"""Authenticated warehouse workflow composition; production provider dependency is absent."""
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import hashlib
import secrets
from fastapi import HTTPException, Request

from app.auth.config import require_secret
from app.db import query_governance_repository as budgets, warehouse_repository as warehouse
from app.db import warehouse_query_send_repository as repo, warehouse_query_attempt_repository as attempts
from app.query_governance.policy import QueryPolicy
from app.query_governance.types import QueryEstimate, EstimateConfidence
from app.query_governance.admission import decide
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_dispatch_checkpoint_service import _session_locked, checkpoint_authenticated_warehouse_dispatch
from app.services import warehouse_sql_stored_draft_service as drafts
from app.services import warehouse_sql_send_recovery_service as recovery
from app.services import warehouse_sql_provider_policy_service as policy
from app.services.warehouse_sql_attested_accounting_service import settle_verified_warehouse_terminal
from app.services.metric_warehouse_binding_service import _source, _context, _publication_state_id
from app.warehouse.sql_draft_planner import plan_draft
from app.warehouse.sql_physical_binding import bind_draft
from app.warehouse.sql_single_model_compiler import compile_single_model_sql
from app.warehouse.sql_provider_metadata_verifier import verify_serving_table_metadata
from app.warehouse.providers.model_adapter import schema as physical_schema
from app.warehouse.query_recovery_attestation import digest
from app.warehouse.query_job_request import build_reviewed_query_job_request
from app.warehouse.query_submission_transport import submit_exact_job_attempt_after_boundary
from app.warehouse.query_cancellation_transport import cancel_exact_job_once_after_intent


@dataclass(frozen=True)
class InjectedQueryRuntime:
    """Trusted dependency injection only; never accepted from HTTP or env flags.

    No default client/factory exists. Tests inject offline provider transports.
    This type alone is not authorization for live cloud execution.
    """
    metadata_client: object
    estimator: object
    submit_transport: object
    results_transport: object
    cancel_transport: object
    lease_seconds: int = 30
    worker_router: object | None = None

    def __post_init__(self):
        if type(self.lease_seconds) is not int or not 1<=self.lease_seconds<=120:
            raise ValueError('Bounded recovery lease required')
        if self.worker_router is not None:
            from app.services.warehouse_worker_router import WarehouseWorkerRouter
            if type(self.worker_router) is not WarehouseWorkerRouter:
                raise ValueError('Independent warehouse worker router required')


# Trusted process-startup registration only. Never construct Google credentials,
# workers, or policies as a side effect of an HTTP request or environment flag.
_reviewed_live_runtime: InjectedQueryRuntime | None = None


def bind_reviewed_query_runtime(runtime: InjectedQueryRuntime) -> None:
    """Install one preverified live runtime from trusted application startup.

    The binding is intentionally separate from credential provisioning.
    Real workers and reviewed project-specific cloud policies must already
    exist. An offline test runtime, or a configuration with one missing
    independent duty, can never become the production dependency.
    """
    from app.services import warehouse_sql_recovery_attestation_service as attestation
    from app.services.warehouse_worker_router import WarehouseWorkerRouter
    from app.warehouse.query_provider_policy import ProviderPolicy
    from app.warehouse.named_query_results import NamedResultPolicy

    if (type(runtime) is not InjectedQueryRuntime
            or type(runtime.worker_router) is not WarehouseWorkerRouter
            or not policy._POLICIES
            or set(policy._POLICIES) != set(policy._RESULT_POLICIES)):
        raise ValueError("REVIEWED_LIVE_WAREHOUSE_RUNTIME_REQUIRED")
    for workspace, provider_policy in policy._POLICIES.items():
        if (type(workspace) is not int or workspace < 1
                or type(provider_policy) is not ProviderPolicy
                or type(policy._RESULT_POLICIES.get(workspace)) is not NamedResultPolicy):
            raise ValueError("REVIEWED_WORKSPACE_POLICY_REQUIRED")
        # Validates immutable expected policies, creator and attester identity,
        # and project/location binding against the separately supplied client.
        policy._runtime(workspace)
    if not all(callable(getattr(runtime.submit_transport, name, None))
               for name in ("insert_job_once",)):
        raise ValueError("LIVE_SENDER_TRANSPORT_REQUIRED")
    if not callable(getattr(runtime.estimator, "estimate", None)):
        raise ValueError("LIVE_ESTIMATOR_REQUIRED")
    if not callable(getattr(runtime.metadata_client, "get_table", None)):
        raise ValueError("LIVE_METADATA_CLIENT_REQUIRED")
    if not callable(getattr(runtime.results_transport, "bind", None)):
        raise ValueError("LIVE_NAMED_RESULT_READER_REQUIRED")
    # Signed RPC health checks perform no provider IO or SQL submission.
    if set(runtime.worker_router.probe_workers()) != {
            "provider_submission", "recovery_attestation",
            "accounting", "result_authorization"}:
        raise ValueError("INDEPENDENT_WORKER_HEALTH_REQUIRED")
    global _reviewed_live_runtime
    if _reviewed_live_runtime is not None:
        raise RuntimeError("LIVE_WAREHOUSE_RUNTIME_ALREADY_BOUND")
    _reviewed_live_runtime = runtime


def get_query_runtime() -> InjectedQueryRuntime:
    # Absent explicit trusted startup setup, every production request stays 503.
    if _reviewed_live_runtime is None:
        raise HTTPException(503, "Warehouse query execution is disabled")
    return _reviewed_live_runtime


def _token(request):
    settings=require_secret()
    if settings.mode!='session': raise HTTPException(503,'Session authentication required')
    token=request.cookies.get(settings.session_cookie)
    if not token or len(token)>128: raise HTTPException(401,'Sign in to continue.')
    return token


@contextmanager
def _scope(request, workspace):
    with repo.transaction(workspace) as conn:
        user,session=_session_locked(conn,_token(request))
        w=repo.one(conn,'SELECT owner,status FROM workspaces WHERE workspace_id=%s FOR SHARE',(workspace,))
        if not w or w['owner']!=user['owner_key'] or w['status']!='ACTIVE':
            raise HTTPException(403,'Workspace unavailable')
        yield conn,user,session


def _owned(conn,user,session,workspace,handle):
    a=attempts.read_attempt(conn,workspace_id=workspace,owner_key=user['owner_key'],attempt_handle=handle)
    binding=repo.one(conn,'SELECT * FROM warehouse_query_api_requests WHERE attempt_id=%s',(a['attempt_id'],))
    if not binding or binding['user_id']!=user['user_id'] or binding['session_digest']!=session['token_digest']:
        raise HTTPException(403,'Original query session required')
    if repo.one(conn,'SELECT 1 FROM warehouse_query_dispatch_revocations WHERE attempt_id=%s',(a['attempt_id'],)):
        raise HTTPException(403,'Query access revoked')
    if binding.get('contract_version',1)==2 and workspace not in policy._RESULT_POLICIES:
        raise HTTPException(503,'Named result policy unavailable')
    return a


def _resolve(conn,user,workspace,model,sql):
    catalog=drafts._catalog_for_owned_model(conn,workspace,user['owner_key'],user,model)
    draft=plan_draft(sql,workspace_id=workspace,catalog=catalog)
    if draft.model_id!=model: raise ValueError('Selected model differs from SQL')
    rows=warehouse.metric_warehouse_bindings(conn,workspace,
        [s['dataset_id'] for s in catalog['models'][0]['sources'] if s['purpose']=='PRIMARY'],environment='development')
    rows=[r for r in rows if (r['warehouse_model_id'],r['warehouse_model_version_id'])==(draft.model_id,draft.model_version_id)]
    if len(rows)!=1: raise ValueError('One current materialized publication required')
    row=rows[0]
    binding=bind_draft(draft,fact=_source(row,workspace,_publication_state_id(row)))
    query=compile_single_model_sql(workspace_id=workspace,draft=draft,binding=binding)
    return draft,binding,query,physical_schema(_context(row))



def _revalidate(conn,user,workspace,model,sql,before):
    try:
        current=_resolve(conn,user,workspace,model,sql)
    except ValueError:
        raise HTTPException(409,'Publication changed; validate again') from None
    if current!=before:
        raise HTTPException(409,'Publication changed; validate again')
    return current


def _preflight(runtime,resolved,workspace):
    draft,binding,query,schema=resolved
    server,_=policy._runtime(workspace)
    verify_serving_table_metadata(binding,workspace_id=workspace,
        expected_schemas={draft.model_id:schema},client=runtime.metadata_client,
        project=server.project,location=server.location)
    named=policy._RESULT_POLICIES.get(workspace)
    if named is not None:
        from app.warehouse.named_query_results import validate_fixed_schema,verify_dataset,result_identity,projection_schema
        validate_fixed_schema(projection_schema(binding.columns),draft.row_limit)
        verify_dataset(policy._runtime(workspace)[1].transport._client,result_identity(server.project,workspace,'preflight'),server.location)
    estimate=runtime.estimator.estimate(query)
    if (type(estimate) is not QueryEstimate or type(estimate.bytes_processed) is not int
            or estimate.bytes_processed<1 or estimate.confidence not in (EstimateConfidence.EXACT,EstimateConfidence.UPPER_BOUND)):
        raise ValueError('Bounded trustworthy estimate required')
    if estimate.bytes_processed>server.maximum_bytes:
        raise HTTPException(409,'Estimate exceeds the server provider byte limit')
    return estimate


def validate_query(request,workspace,model,sql,runtime):
    with _scope(request,workspace) as (conn,user,_): before=_resolve(conn,user,workspace,model,sql)
    estimate=_preflight(runtime,before,workspace)
    with _scope(request,workspace) as (conn,user,_):
        _revalidate(conn,user,workspace,model,sql,before)
        decision=decide(estimate,budgets._snapshot_locked(conn,workspace),requested_rows=before[0].row_limit,policy=QueryPolicy.from_environment())
    return dict(valid=True,model_id=model,model_version_id=before[0].model_version_id,
        columns=list(before[0].projected_columns),row_limit=before[0].row_limit,
        estimated_bytes=estimate.bytes_processed,budget_decision=decision.decision.value,reason=decision.reason,
        query_execution_enabled=False)


def admit_query(request,workspace,model,sql,key,runtime):
    key_sha=hashlib.sha256(key.encode()).hexdigest(); request_sha=digest(dict(model=model,sql=sql.strip()))
    with _scope(request,workspace) as (conn,user,session):
        old=repo.one(conn,'SELECT * FROM warehouse_query_api_requests WHERE workspace_id=%s AND user_id=%s AND key_sha256=%s',
            (workspace,user['user_id'],key_sha))
        if old:
            if old['request_sha256']!=request_sha: raise HTTPException(409,'Idempotency key conflicts with original request')
            a=repo.one(conn,'SELECT attempt_handle FROM warehouse_query_attempt_registry WHERE attempt_id=%s',(old['attempt_id'],))
            _owned(conn,user,session,workspace,a['attempt_handle'].strip())
            return dict(attempt_handle=a['attempt_handle'].strip(),reused=True,query_execution_enabled=False)
        before=_resolve(conn,user,workspace,model,sql)
    estimate=_preflight(runtime,before,workspace)
    with _scope(request,workspace) as (conn,user,session):
        # Serialize duplicate admission after provider IO and recheck all source authority.
        old=repo.one(conn,'SELECT * FROM warehouse_query_api_requests WHERE workspace_id=%s AND user_id=%s AND key_sha256=%s',
            (workspace,user['user_id'],key_sha))
        if old:
            if old['request_sha256']!=request_sha or old['session_digest']!=session['token_digest']:
                raise HTTPException(409,'Idempotency request conflicts')
            a=repo.one(conn,'SELECT attempt_handle FROM warehouse_query_attempt_registry WHERE attempt_id=%s',(old['attempt_id'],))
            _owned(conn,user,session,workspace,a['attempt_handle'].strip())
            return dict(attempt_handle=a['attempt_handle'].strip(),reused=True,query_execution_enabled=False)
        if workspace in policy._RESULT_POLICIES:
            held=conn.execute('SELECT count(*) FROM warehouse_query_result_contracts WHERE workspace_id=%s',(workspace,)).fetchone()[0]
            if held>=8:raise HTTPException(409,'Named result storage is permanently held; review capacity before admission')
        current=_revalidate(conn,user,workspace,model,sql,before)
        draft,binding,query,_=current
        a,_=attempts.register_attempt(conn,workspace_id=workspace,owner_key=user['owner_key'],
            attempt_handle=secrets.token_hex(16),logical_intent_sha256=drafts.logical_draft_digest(draft))
        governance=QueryGovernanceService(workspace,user)
        drafts._register_in_transaction(conn,governance,a['attempt_handle'].strip(),model,sql)
        result=budgets.reserve(conn,workspace_id=workspace,owner_key=user['owner_key'],consumer='SQL',provider='BIGQUERY',
            request_token=a['request_token'].strip(),intent_sha256=query.intent_sha256,estimate=estimate,
            requested_rows=draft.row_limit,policy=QueryPolicy.from_environment())
        if not result['reservation']: raise HTTPException(409,'Query budget requires review or is exhausted')
        for pin in binding.models:
            conn.execute('INSERT INTO warehouse_query_attempt_publication_pins(attempt_id,resource_id,publication_id,publication_revision) VALUES(%s,%s,%s,%s)',
                (a['attempt_id'],pin.resource_id,pin.publication_id,pin.publication_revision))
        conn.execute('INSERT INTO warehouse_query_attempt_reservation_links(attempt_id,reservation_id) VALUES(%s,%s)',
            (a['attempt_id'],result['reservation']['reservation_id']))
        named=policy._RESULT_POLICIES.get(workspace)
        if named is None:
            conn.execute('INSERT INTO warehouse_query_api_requests(workspace_id,user_id,key_sha256,request_sha256,session_digest,attempt_id) VALUES(%s,%s,%s,%s,%s,%s)',
                (workspace,user['user_id'],key_sha,request_sha,session['token_digest'],a['attempt_id']))
        else:
            from app.warehouse.named_query_results import result_identity,validate_fixed_schema,STORAGE_BYTES,WORKSPACE_STORAGE_BYTES,projection_schema
            schema=projection_schema(binding.columns)
            validate_fixed_schema(schema,draft.row_limit)
            conn.execute('INSERT INTO warehouse_query_api_requests(workspace_id,user_id,key_sha256,request_sha256,session_digest,attempt_id,contract_version) VALUES(%s,%s,%s,%s,%s,%s,2)',
                (workspace,user['user_id'],key_sha,request_sha,session['token_digest'],a['attempt_id']))
            job=f"dr_query_w{workspace}_r{result['reservation']['reservation_id']}_{a['request_token'].strip()[:32]}"
            until_ms=conn.execute('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint+%s',(named.read_seconds*1000,)).fetchone()[0]
            contract=dict(version=1,destination=result_identity(policy._runtime(workspace)[0].project,workspace,job),
                schema=[list(c) for c in schema],maximum_rows=draft.row_limit,maximum_storage_bytes=STORAGE_BYTES,
                workspace_storage_bytes=WORKSPACE_STORAGE_BYTES,read_until_ms=until_ms,policy=asdict(named))
            from psycopg.types.json import Jsonb
            conn.execute('INSERT INTO warehouse_query_result_contracts(attempt_id,workspace_id,provider_job_id,sql_sha256,result,read_valid_until) VALUES(%s,%s,%s,%s,%s,to_timestamp(%s/1000.0))',
                (a['attempt_id'],workspace,job,hashlib.sha256(query.sql.encode()).hexdigest(),Jsonb(contract),until_ms))
    return dict(attempt_handle=a['attempt_handle'].strip(),reused=False,query_execution_enabled=False)


def query_status(request,workspace,handle):
    with _scope(request,workspace) as (conn,user,session):
        a=_owned(conn,user,session,workspace,handle)
        r=repo.one(conn,'''SELECT r.*,r.expires_at<=clock_timestamp() AS expired
            FROM warehouse_query_attempt_reservation_links l JOIN query_governance_reservations r USING(reservation_id)
            WHERE l.attempt_id=%s''',(a['attempt_id'],))
        j=repo.one(conn,'SELECT * FROM warehouse_query_send_journal WHERE attempt_id=%s ORDER BY revision DESC LIMIT 1',(a['attempt_id'],))
        state='UNCERTAIN'
        if r['status']=='ACTIVE': state='EXPIRED' if r['expired'] else 'ADMITTED'
        elif r['status']=='COMPLETED':
            from app.services.warehouse_sql_result_eligibility_service import _inspect_settled_result_eligibility_locked
            try:
                _inspect_settled_result_eligibility_locked(conn,a['attempt_id']);state='SUCCEEDED'
            except ValueError as error:
                if str(error)=='NAMED_RESULT_READ_WINDOW_EXPIRED':state='EXPIRED'
        elif r['status']=='FAILED' and j:
            proof=repo.one(conn,'''SELECT a.*,p.expected_sha256,u.provider_job_id AS ledger_job,u.outcome,
                i.provider_job_id AS original_job,u.bytes_processed AS ledger_processed,u.bytes_billed AS ledger_billed,
                u.accounting_basis,u.accounted_bytes
                FROM warehouse_query_recovery_attestations a JOIN warehouse_query_provider_policies p USING(attempt_id)
                JOIN warehouse_query_send_intents i USING(attempt_id)
                JOIN query_governance_usage u ON u.reservation_id=%s
                WHERE a.attempt_id=%s AND a.journal_revision=%s''',(r['reservation_id'],a['attempt_id'],j['revision']))
            if (proof and proof['observation'] in ('FAILED','CANCELLED')
                    and j['phase']==proof['observation']+'_OBSERVED' and proof['accounting_basis']=='ACTUAL'
                    and proof['verdict']=='VERIFIED_METADATA' and proof['billing_complete']
                    and type(proof['bytes_processed']) is int and type(proof['bytes_billed']) is int
                    and proof['accounted_bytes']==max(proof['bytes_processed'],proof['bytes_billed'])
                    and proof['policy_sha256']==proof['expected_sha256'] and proof['ledger_job']==proof['original_job']
                    and proof['outcome']=='FAILED' and proof['ledger_processed']==proof['bytes_processed']
                    and proof['ledger_billed']==proof['bytes_billed']):
                state='CANCELLED' if proof['observation']=='CANCELLED' else 'FAILED'
        elif j:
            if j['phase']=='INTENT_RECORDED':state='PREPARED'
            elif j['phase']=='PROVIDER_RUNNING':state='RUNNING'
            # A terminal observation without committed accounting is never successful.
        return dict(attempt_handle=handle,status=state,results_available=state=='SUCCEEDED',
            query_execution_enabled=False)


def _lease(attempt,runtime):
    # The real multi-process path uses the separately authenticated attester
    # rather than borrowing its PostgreSQL role inside the API process.
    router=getattr(runtime,"worker_router",None)
    if router is not None:
        view=router.inspect_recovery(attempt)
        return router.acquire_recovery(
            attempt,revision=view.revision,seconds=runtime.lease_seconds)
    view=recovery.inspect_recovery(attempt_id=attempt)
    try: return recovery.acquire_recovery(attempt_id=attempt,expected_revision=view.revision,lease_seconds=runtime.lease_seconds)
    except repo.RecoveryConflict as error:
        raise HTTPException(409,'Query is busy; retry status or refresh later') from error


def _observe_and_settle(request,workspace,handle,lease,revision,runtime=None):
    with recovery._worker(lease.attempt_id) as (conn,c):
        _,event=repo.latest(conn,lease.attempt_id)
        terminal=event['phase'] in ('SUCCEEDED_OBSERVED','FAILED_OBSERVED','CANCELLED_OBSERVED')
        if terminal:
            # F2's initial-send lookup deliberately excludes terminal phases.
            # Reuse the terminal gate to validate the committed envelope/boundary,
            # then E3's token-fenced GET-only re-attestation for missing billing.
            from app.services.warehouse_sql_terminal_evidence_service import _inspect_terminal_evidence_locked
            _inspect_terminal_evidence_locked(conn,lease.attempt_id)
    lookup=policy.lookup_with_provider_policy if terminal else policy.lookup_pinned_submission_without_resend
    observation=lookup(lease=lease,expected_revision=revision)
    # Reauthorize before settlement/response; observation remains useful trusted recovery metadata.
    query_status(request,workspace,handle)
    if observation.verdict=='VERIFIED_METADATA' and observation.billing_complete and observation.observation in ('SUCCEEDED','FAILED','CANCELLED'):
        if runtime is not None and getattr(runtime,"worker_router",None) is not None:
            runtime.worker_router.settle_terminal(lease.attempt_id)
        else:
            settle_verified_warehouse_terminal(attempt_id=lease.attempt_id)
    return query_status(request,workspace,handle)


def execute_query(request,workspace,handle,runtime):
    current=query_status(request,workspace,handle)
    if current['status'] in ('SUCCEEDED','FAILED','CANCELLED'):return current
    with _scope(request,workspace) as (conn,user,session):
        a=_owned(conn,user,session,workspace,handle)
        draft=repo.one(conn,'SELECT * FROM warehouse_query_attempt_logical_drafts WHERE attempt_id=%s',(a['attempt_id'],))
        sent=repo.one(conn,"SELECT 1 FROM warehouse_query_send_journal WHERE attempt_id=%s AND event='SEND_BOUNDARY'",(a['attempt_id'],))
        before=None if sent else _resolve(conn,user,workspace,draft['warehouse_model_id'],draft['normalized_sql'])
    if sent:return refresh_query(request,workspace,handle,runtime)
    # Fresh verification before claim; never enlarge the original reservation.
    estimate=_preflight(runtime,before,workspace)
    with _scope(request,workspace) as (conn,user,session):
        _owned(conn,user,session,workspace,handle)
        _revalidate(conn,user,workspace,draft['warehouse_model_id'],draft['normalized_sql'],before)
        original=repo.one(conn,'''SELECT r.* FROM warehouse_query_attempt_reservation_links l
            JOIN query_governance_reservations r USING(reservation_id) WHERE l.attempt_id=%s''',(a['attempt_id'],))
        if original['intent_sha256']!=before[2].intent_sha256 or estimate.bytes_processed>original['reserved_bytes']:
            raise HTTPException(409,'Original query input or reserved budget is stale')
    if workspace in policy._RESULT_POLICIES:
        with _scope(request,workspace) as (conn,user,session):
            nr=repo.one(conn,'SELECT read_valid_until>clock_timestamp() AS valid FROM warehouse_query_result_contracts WHERE attempt_id=%s',(a['attempt_id'],))
            if not nr or not nr['valid']:raise HTTPException(409,'Named result admission is expired or unavailable')
    checkpoint_authenticated_warehouse_dispatch(request=request,workspace_id=workspace,attempt_handle=handle)
    recovery.prepare_authenticated_send_intent(request=request,workspace_id=workspace,attempt_handle=handle)
    policy.pin_authenticated_query_envelope_without_dispatch(request=request,workspace_id=workspace,attempt_handle=handle)
    lease=_lease(a['attempt_id'],runtime)
    with recovery._worker(a['attempt_id']) as (conn,c):
        _,q,_=recovery._live_context(conn,c)
        expected,_,_=policy._snapshot(conn,c,lease,lease.revision)
        job=build_reviewed_query_job_request(expected=expected,compiled_sql=q.sql)
        envelope=repo.one(conn,'SELECT request_sha256 FROM warehouse_query_request_envelopes WHERE attempt_id=%s',(a['attempt_id'],))
        if envelope['request_sha256']!=digest(job.body): raise ValueError('Original request fingerprint differs')
    if getattr(runtime, "worker_router", None) is not None:
        # A live worker cannot inspect user/session/publication tables. The
        # authenticated query-control identity owns the durable send fence.
        # Keep that transaction committed before any external provider I/O.
        # The FastAPI route already owns the restricted query-control boundary.
        # Opening another one here is forbidden by connection_boundary().
        marker = recovery.record_authenticated_control_send_boundary_without_dispatch(
            request=request, workspace_id=workspace, attempt_handle=handle,
            lease=lease, expected_revision=lease.revision,
        )
    else:
        # Offline injected test runtimes retain the original deterministic path.
        marker = recovery.record_pinned_send_boundary_without_dispatch(
            lease=lease, expected_revision=lease.revision,
        )
    if marker.job != lease.job or marker.phase != "SEND_UNKNOWN":
        raise repo.RecoveryConflict("PINNED_SEND_BOUNDARY_NOT_VERIFIED")
    # A second request must recover the original job, never submit again.
    submit_exact_job_attempt_after_boundary(request=job,transport=runtime.submit_transport)
    return _observe_and_settle(request,workspace,handle,lease,marker.revision,runtime)


def refresh_query(request,workspace,handle,runtime):
    current=query_status(request,workspace,handle)
    if current['status'] in ('SUCCEEDED','FAILED','CANCELLED'): return current
    with _scope(request,workspace) as (conn,user,session):
        a=_owned(conn,user,session,workspace,handle)
        if not repo.one(conn,"SELECT 1 FROM warehouse_query_send_journal WHERE attempt_id=%s AND event='SEND_BOUNDARY'",(a['attempt_id'],)):
            return current
        event=repo.one(conn,'SELECT * FROM warehouse_query_send_journal WHERE attempt_id=%s ORDER BY revision DESC LIMIT 1',(a['attempt_id'],))
        report=repo.one(conn,'SELECT * FROM warehouse_query_recovery_attestations WHERE attempt_id=%s AND journal_revision=%s',(a['attempt_id'],event['revision']))
    # Recover a successful observation whose accounting commit/response was lost, without another GET or lease.
    if report and report['verdict']=='VERIFIED_METADATA' and report['billing_complete'] and report['observation'] in ('SUCCEEDED','FAILED','CANCELLED'):
        if getattr(runtime,"worker_router",None) is not None:
            runtime.worker_router.settle_terminal(a['attempt_id'])
        else:
            settle_verified_warehouse_terminal(attempt_id=a['attempt_id'])
        return query_status(request,workspace,handle)
    lease=_lease(a['attempt_id'],runtime)
    return _observe_and_settle(request,workspace,handle,lease,lease.revision,runtime)


def cancel_query(request,workspace,handle,runtime):
    from app.services.warehouse_sql_cancel_intent_service import record_cancel_intent_without_provider
    from app.services.warehouse_sql_cancel_attempt_boundary_service import record_cancel_attempt_boundary_without_provider
    current=query_status(request,workspace,handle)
    if current['status'] in ('SUCCEEDED','FAILED','CANCELLED'):return current
    if current['status'] in ('ADMITTED','PREPARED','EXPIRED'):
        raise HTTPException(409,'Only a possibly sent job can request cancellation')
    with _scope(request,workspace) as (conn,user,session):
        a=_owned(conn,user,session,workspace,handle)
        already=repo.one(conn,'SELECT 1 FROM warehouse_query_cancel_attempt_boundaries WHERE attempt_id=%s',(a['attempt_id'],))
    if already:return refresh_query(request,workspace,handle,runtime)
    lease=_lease(a['attempt_id'],runtime)
    record_cancel_intent_without_provider(lease=lease,expected_revision=lease.revision)
    record_cancel_attempt_boundary_without_provider(lease=lease,expected_revision=lease.revision)
    cancel_exact_job_once_after_intent(project=lease.job.project,location=lease.job.location,
        job_id=lease.job.job_id,transport=runtime.cancel_transport)
    return _observe_and_settle(request,workspace,handle,lease,lease.revision,runtime)


def query_results(request,workspace,handle,runtime):
    from app.services.warehouse_sql_server_result_schema_service import inspect_server_result_schema
    from app.services.warehouse_sql_authenticated_first_page_service import read_authenticated_first_warehouse_page_simulated
    from app.warehouse.query_bigquery_first_page_bridge import SimulatedBigQueryFirstPage
    with _scope(request,workspace) as (conn,user,session):
        a=_owned(conn,user,session,workspace,handle)
    if query_status(request,workspace,handle)['status']!='SUCCEEDED':
        raise HTTPException(409,'Settled successful results are not ready')
    router=getattr(runtime,"worker_router",None)
    if router is not None:
        # Establish exact immutable job identity inside the authenticated
        # control boundary; legacy offline test runtimes remain unchanged.
        with _scope(request,workspace) as (conn,user,session):
            _owned(conn,user,session,workspace,handle)
            identity=repo.one(conn,'''SELECT l.reservation_id,i.provider_job_id,i.provider_project,i.provider_location
                FROM warehouse_query_attempt_reservation_links l
                JOIN warehouse_query_send_intents i USING(attempt_id)
                WHERE l.attempt_id=%s''',(a['attempt_id'],))
        eligibility=router.inspect_eligibility(a['attempt_id'])
        if (not identity or eligibility.attempt_id!=a['attempt_id']
                or eligibility.reservation_id!=identity['reservation_id']
                or eligibility.job_id!=identity['provider_job_id']
                or eligibility.project!=identity['provider_project']
                or eligibility.location!=identity['provider_location']
                or eligibility.classification!='SETTLED_SUCCESS_RESULTS_REVIEW_REQUIRED'
                or eligibility.provider_dispatched is not False
                or eligibility.result_fetched is not False):
            raise HTTPException(409,'Independent result authorization disagrees')
    schema=inspect_server_result_schema(attempt_id=a['attempt_id'])
    reader=runtime.results_transport
    with _scope(request,workspace) as (conn,user,session):
        expected=repo.one(conn,'SELECT expected FROM warehouse_query_provider_policies WHERE attempt_id=%s',(a['attempt_id'],))['expected']
        if expected['contract_version']==2:
            from app.warehouse.named_query_results import IndependentNamedResultReader
            from app.warehouse.query_bigquery_named_table_reader import PinnedNamedTableResultReaderFactory
            if [list(col) for col in schema.columns]!=expected['result']['schema']:raise ValueError('Pinned result schema changed')
            proof=repo.one(conn,'SELECT metadata_sha256 FROM warehouse_query_result_attestations WHERE attempt_id=%s ORDER BY journal_revision DESC LIMIT 1',(a['attempt_id'],))
            if not proof:raise ValueError('Pinned materialization proof required')
            # Bind job-specific table identity ONLY after authenticated schema
            # and stored immutable policy have been read from PostgreSQL.
            # Tests may still inject the existing fake results transport.
            if isinstance(reader, PinnedNamedTableResultReaderFactory):
                # Trusted local adapters may refresh a short-lived reader
                # token at the exact pinned-job bind; the concrete factory
                # still validates the independent reader principal.
                reader=reader.bind(expected)
            reader=IndependentNamedResultReader(expected,proof['metadata_sha256'],policy._runtime(workspace)[1].transport._client,reader)
    page=read_authenticated_first_warehouse_page_simulated(request=request,workspace_id=workspace,attempt_handle=handle,
        transport=SimulatedBigQueryFirstPage(reader,schema.columns))
    query_status(request,workspace,handle)  # Final session/revocation check before serialization.
    return dict(attempt_handle=handle,columns=list(page.columns),rows=[list(row) for row in page.rows],
        has_more=page.provider_has_more,bytes_encoded=page.bytes_encoded,query_execution_enabled=False)
