"""Explicit owned 11F orchestration; no route, startup hook or consumer switch."""
from types import SimpleNamespace
from uuid import UUID

from app.db import warehouse_repository as metadata
from app.db import warehouse_reliability_repository as db
from app.schemas.warehouse_modeling import SourceSchema
from app.services.warehouse_modeling_service import ADAPTER, check_configuration
from app.services.warehouse_reliability_service import ReliabilityService
from app.warehouse.accepted_changes import digest
from app.warehouse.providers.base import Binding, ErrorCode, Policy, ProviderError, TokenState
from app.warehouse.reliability import Receipt, Result
from app.warehouse.fact_planning import FactContext
from app.warehouse.providers import model_adapter
from app.warehouse.scd_types import ModelContext, SourceContractPin
from app.warehouse.access import owned_version


class ProviderService:
    def __init__(self, workspace_id, model_id, version_id, user, *, target_factory=None):
        self.reliability = ReliabilityService(workspace_id, model_id, version_id, user)
        self.target_factory = target_factory

    def _target(self, binding):
        # Resolve policy/credentials only after current ownership and scope checks.
        policy = Policy.from_environment()
        if self.target_factory:
            return self.target_factory(policy, binding)
        from app.warehouse.providers.bigquery import BigQueryTarget
        return BigQueryTarget(policy, binding)

    def _resolve(self, conn, config, settings, resource_id):
        w, m, v = self.reliability.scope
        check_configuration(conn, w, m, v)
        model, _ = owned_version(conn, w, m, v, self.reliability.user, True)
        resource = metadata.resource(conn, w, m, v, resource_id)
        if not resource or resource['status'] != 'REGISTERED' or resource['environment'] != settings.environment:
            raise ProviderError(ErrorCode.RESOURCE_NOT_FOUND)
        reference = resource['physical_reference']
        binding = Binding(reference['project'], w, m, v, resource_id, resource['generation'],
                          resource['environment'], resource['location'])
        if reference != dict(reference_version=1, project=binding.project, dataset=binding.serving, table=binding.table):
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        typed = ADAPTER.validate_python(
            config['configuration']
        )

        source_schema = SourceSchema.model_validate(
            config['source_schema']
        )

        source_contract = SourceContractPin(
            config['source_dataset_id'],
            config['source_dataset_version_id'],
            config['source_policy_id'],
            config['source_rule_version'],
        )

        namespace = UUID(
            str(model['key_namespace'])
        )

        if typed.model_type == 'FACT':
            context = FactContext(
                m,
                v,
                namespace,
                typed,
                source_schema,
                source_contract,
                configuration_sha256=digest(
                config["configuration"]
            ),
            )
        else:
            context = ModelContext(
                m,
                v,
                namespace,
                typed,
                source_schema,
                source_contract,
            )

        return binding, context
    @staticmethod
    def _state_key(context, state):
        if isinstance(context, FactContext):
            return state.fact_sk

        if isinstance(context, ModelContext):
            return state.member_sk

        raise ProviderError(
            ErrorCode.SCHEMA_MISMATCH
        )
    def provision(self, resource_id):
        with self.reliability.transaction() as (conn, source, config, settings):
            binding, context = self._resolve(conn, config, settings, resource_id)
            self._target(binding).provision(context)

    def _prior(self, conn, target, context, resource_id, keys):
        """Bounded revision overlay. Unpublished candidates never participate."""
        result = {}
        head = db.head(conn, resource_id)
        publication_id = head['publication_id'] if head else None
        depth = 0
        while publication_id and keys - result.keys():
            depth += 1
            if depth > 100:
                raise ValueError('Candidate lineage exceeds bounded 11F depth; explicit compaction required')
            pub = db.one(conn, 'SELECT * FROM warehouse_publications WHERE publication_id=%s AND resource_id=%s',
                         (publication_id, resource_id))
            if not pub:
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            intent = db.intent(conn, pub['operation_id'])
            # No caller-supplied physical identifier or SQL; reconstruct the exact
            # old immutable token address from its scoped PostgreSQL intent.
            address = SimpleNamespace(binding=target.binding, operation_id=pub['operation_id'],
                application_token=intent['application_token'], intent_sha256=intent['intent_sha256'],
                scope_hash=digest([target.binding.__dict__, pub['operation_id']]),
                plan=SimpleNamespace(context=context), evidence=dict(application_token=intent['application_token']))
            for state in target.read_published_candidate(address, Result.model_validate(intent['body']['expected'])):
                            key = self._state_key(
                context,
                state,
            )

            if key in keys and key not in result:
                result[key] = state
            publication_id = pub['previous_publication_id']
        return tuple(result[k] for k in sorted(result))

    def prepare(self, resource_id, manifests):
        """Obtain expected result before creating an 11E immutable intent."""
        with self.reliability.transaction() as (conn, source, config, settings):
            binding, context = self._resolve(conn, config, settings, resource_id)
            self._registered(conn, manifests, source['source_mapping_id'])
            target = self._target(binding)
            keys = self._keys(context, manifests)
            return model_adapter.compile_plan(
                context,
                manifests,
                self._prior(
                    conn,
                    target,
                    context,
                    resource_id,
                    keys,
                ),
            )

    @staticmethod
    def _keys(context, manifests):
        from app.warehouse.keys import surrogate_key
        from app.warehouse.providers.types import scalar

        if isinstance(context, FactContext):
            role = 'FACT'
            columns = (
                context.configuration
                .grain
                .identity_columns
            )

        elif isinstance(context, ModelContext):
            role = 'DIMENSION_MEMBER'
            columns = (
                context.configuration
                .business_keys
            )

        else:
            raise ProviderError(
                ErrorCode.SCHEMA_MISMATCH
            )

        result = set()

        for manifest in manifests:
            for accepted in manifest['events']:
                values = {
                    name: scalar(value)
                    for name, value
                    in accepted['payload']
                }

                result.add(
                    surrogate_key(
                        context.namespace,
                        role,
                        columns,
                        values,
                        algorithm_version=(
                            context.key_algorithm_version
                        ),
                    )
                )

        return result

    @staticmethod
    def _registered(conn, manifests, mapping):
        if not 1 <= len(manifests) <= 1000 or len({m['manifest_id'] for m in manifests}) != len(manifests):
            raise ValueError('Bounded distinct registered manifests required')
        for m in manifests:
            row = db.one(conn, 'SELECT body FROM warehouse_accepted_manifests WHERE manifest_id=%s AND source_mapping_id=%s',
                         (m['manifest_id'], mapping))
            if not row or row['body'] != m:
                raise ProviderError(ErrorCode.PERMISSION_DENIED)

    def execute(self, operation_id, run_id, fence):
        from app.warehouse.providers.bigquery import Batch
        with self.reliability.transaction() as (conn, source, config, settings):
            op, intent = self.reliability._writer(conn, operation_id, run_id, fence, settings)
            binding, context = self._resolve(conn, config, settings, intent['resource_id'])
            manifests = [db.one(conn, 'SELECT body FROM warehouse_accepted_manifests WHERE manifest_id=%s',
                                (m['id'],))['body'] for m in intent['body']['manifests']]
            self._registered(conn, manifests, source['source_mapping_id'])
            target = self._target(binding)
            plan = model_adapter.compile_plan(
                context,
                manifests,
                self._prior(
                    conn,
                    target,
                    context,
                    intent['resource_id'],
                    self._keys(
                        context,
                        manifests,
                    ),
                ),
            )
            if plan.result != Result.model_validate(intent['body']['expected']):
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            batch = Batch(binding, operation_id, intent['application_token'], intent['intent_sha256'], plan,
                          operation_type=intent['body']['kind'])
            status = target.lookup(batch)
            prior_stage = db.latest_stage(conn, operation_id)
        if status == TokenState.NOT_APPLIED:
            if prior_stage:
                # A prior PLANNED marker is uncertainty, not a retry grant.
                # This receipt is backed by the fresh provider token/job lookup.
                self.reliability.record_receipt(operation_id, run_id, fence,
                    Receipt(operation_id=operation_id, run_id=run_id,
                        application_token=intent['application_token'], intent_sha256=intent['intent_sha256'],
                        resource_generation=binding.generation, outcome='NOT_APPLIED'))
            # Commit PLANNED before any provider mutation, without nesting a
            # second model-locking transaction inside the authorization lock.
            self.reliability.plan(operation_id, run_id, fence)
        elif status != TokenState.APPLIED:
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT, uncertain=True)
        with self.reliability.transaction() as (conn, source, config, settings):
            self.reliability._writer(conn, operation_id, run_id, fence, settings)
            current_binding, _ = self._resolve(conn, config, settings, intent['resource_id'])
            if current_binding != binding:
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            if status == TokenState.APPLIED:
                result = target.reconcile(batch)
            else:
                result = target.apply(batch)
            receipt = Receipt(operation_id=operation_id, run_id=run_id, application_token=intent['application_token'],
                intent_sha256=intent['intent_sha256'], resource_generation=binding.generation,
                outcome='SUCCEEDED', result=result, receipt_reference='bq_' + intent['application_token'])
        self.reliability.record_receipt(operation_id, run_id, fence, receipt)
        reconciliation = self.reliability.reconcile(operation_id, run_id, fence)
        if not reconciliation['passed']:
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        return dict(reconciliation_id=reconciliation['reconciliation_id'], publication_eligible=True,
                    physical_reconciliation=target.last_reconciliation,
                    telemetry=[dict(row, run_id=run_id, fence=fence) for row in target.telemetry])
