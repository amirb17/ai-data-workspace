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
from app.warehouse.serving_materialization import (
    ServingMaterializationRequest, ServingMaterializationResult, configuration_digest,
    materialization_identity,
)
import json


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
                key = self._state_key(context, state)
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

    def _published_lineage(self, conn, binding, publication):
        """Capture only immutable published metadata; perform no provider reads."""
        lineage, seen = [], set()
        current, revision = publication['publication_id'], publication['revision']
        while current is not None:
            if len(lineage) >= 100:
                raise ValueError('Publication lineage exceeds 100; explicit compaction/rebuild required')
            if current in seen:
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            seen.add(current)
            pub = db.one(conn, 'SELECT * FROM warehouse_publications WHERE publication_id=%s AND resource_id=%s',
                         (current, binding.resource_id))
            if not pub or pub['revision'] != revision:
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            intent = db.intent(conn, pub['operation_id'])
            op = metadata.operation(conn, binding.workspace_id, pub['operation_id'])
            expected = dict(workspace_id=binding.workspace_id, warehouse_model_id=binding.model_id,
                warehouse_model_version_id=binding.version_id, resource_id=binding.resource_id)
            if (not intent or not op or any(intent.get(k) != v or op.get(k) != v for k, v in expected.items())
                    or intent['generation'] != binding.generation or op['environment'] != binding.environment
                    or intent['body']['prior_head'] != pub['previous_publication_id']
                    or intent['body']['prior_revision'] != revision - 1):
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            lineage.append((pub, intent))
            current, revision = pub['previous_publication_id'], revision - 1
        if revision != 0:
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        return tuple(lineage)

    def _complete_published_state(self, target, context, lineage):
        """Overlay all published identities, newest first, outside a DB transaction."""
        complete, physical_count, payload_bytes = {}, 0, 0
        if not 1 <= len(lineage) <= 100:
            raise ValueError('Bounded published lineage required; compaction/rebuild may be required')
        for pub, intent in lineage:
            address = SimpleNamespace(binding=target.binding, operation_id=pub['operation_id'],
                application_token=intent['application_token'], intent_sha256=intent['intent_sha256'],
                scope_hash=digest([target.binding.__dict__, pub['operation_id']]),
                plan=SimpleNamespace(context=context), evidence=dict(application_token=intent['application_token']),
                manifest_evidence=json.dumps([[m['id'], m['checksum']] for m in intent['body']['manifests']],
                                             separators=(',', ':')))
            seen = set()
            for state in target.read_published_candidate(address, Result.model_validate(intent['body']['expected'])):
                key = self._state_key(context, state)
                if key in seen or state.context != context:
                    raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
                seen.add(key)
                if key not in complete:
                    physical_rows = model_adapter.rows_from_states(context, (state,))
                    physical_count += len(physical_rows)
                    payload_bytes += len(json.dumps(physical_rows, ensure_ascii=False).encode('utf-8'))
                    if (physical_count > target.policy.maximum_rows
                            or payload_bytes > target.policy.maximum_payload_bytes):
                        raise ProviderError(ErrorCode.COST_LIMIT_EXCEEDED)
                    complete[key] = state
        return tuple(complete[k] for k in sorted(complete))

    @staticmethod
    def _serving_pins(binding, context, publication):
        table = binding.publication_physical(publication['publication_id'], publication['revision'])
        pins = dict(workspace_id=binding.workspace_id, model_id=binding.model_id,
            model_version_id=binding.version_id, resource_id=binding.resource_id, generation=binding.generation,
            publication_id=publication['publication_id'], publication_revision=publication['revision'],
            physical_table=table, configuration_sha256=configuration_digest(context),
            schema_sha256=model_adapter.schema_hash(context), source_vector_sha256=digest(publication['source_vector']))
        return pins | materialization_identity(**pins)

    @staticmethod
    def _check_existing_serving(row, pins):
        if row is None:
            return
        expected = {k: v for k, v in pins.items() if k not in ('model_id', 'model_version_id', 'physical_table')}
        expected.update(warehouse_model_id=pins['model_id'], warehouse_model_version_id=pins['model_version_id'])
        if any(row.get(k) != v for k, v in expected.items()):
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        ServingMaterializationResult(**{k: row[k] for k in (
            'publication_id', 'publication_revision', 'generation', 'materialization_token', 'intent_sha256',
            'provider_job_id', 'serving_sha256', 'row_count', 'bytes_processed', 'bytes_billed')})

    def materialize_current_publication(self, resource_id):
        # Transaction 1: authenticate and capture an exact immutable publication.
        with self.reliability.transaction() as (conn, source, config, settings):
            binding, context = self._resolve(conn, config, settings, resource_id)
            head = db.head(conn, resource_id)
            if not head:
                raise ValueError('WAREHOUSE_PUBLICATION_REQUIRED')
            publication = db.one(conn, 'SELECT * FROM warehouse_publications WHERE resource_id=%s AND publication_id=%s',
                                 (resource_id, head['publication_id']))
            if not publication or publication['revision'] != head['revision']:
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            pins = self._serving_pins(binding, context, publication)
            existing = db.serving_materialization(conn, resource_id, publication['publication_id'])
            self._check_existing_serving(existing, pins)
            lineage = self._published_lineage(conn, binding, publication)

        # ADC/client creation and all candidate/serving reads occur without an
        # open PostgreSQL transaction or publication/model lock.
        target = self._target(binding)
        states = self._complete_published_state(target, context, lineage)
        request = ServingMaterializationRequest(binding=binding, context=context, states=states,
            **{k: v for k, v in pins.items() if k not in ('workspace_id', 'model_id', 'model_version_id', 'resource_id', 'generation')})
        outcome = target.materialize_serving(request)
        if not isinstance(outcome, ServingMaterializationResult):
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        evidence = dict(workspace_id=binding.workspace_id, warehouse_model_id=binding.model_id,
            warehouse_model_version_id=binding.version_id, resource_id=resource_id, **outcome.__dict__,
            configuration_sha256=pins['configuration_sha256'], schema_sha256=pins['schema_sha256'],
            source_vector_sha256=pins['source_vector_sha256'])
        self._check_existing_serving(evidence, pins)
        if outcome.serving_sha256 != request.serving_sha256 or outcome.row_count != request.row_count:
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)

        # Transaction 2: a physically successful old publication is harmless;
        # it becomes eligible only if ownership and the current head still match.
        with self.reliability.transaction() as (conn, source, config, settings):
            current_binding, current_context = self._resolve(conn, config, settings, resource_id)
            head = db.head(conn, resource_id)
            current = db.one(conn, 'SELECT * FROM warehouse_publications WHERE resource_id=%s AND publication_id=%s',
                             (resource_id, publication['publication_id']))
            if (current_binding != binding or current_context != context or not current
                    or not head or (head['publication_id'], head['revision']) != (publication['publication_id'], publication['revision'])
                    or self._serving_pins(current_binding, current_context, current) != pins):
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            return db.record_serving_materialization(conn, evidence)

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
