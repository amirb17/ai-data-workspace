"""Owned, opt-in durable protocol. No scheduler, provider, or consumer integration."""
from contextlib import contextmanager
from datetime import timedelta

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.db.database import repository_transaction
from app.db import warehouse_repository as metadata
from app.db import warehouse_reliability_repository as db
from app.schemas.warehouse import OperationCreate, RunTransition
from app.warehouse.access import owned_version, validate_sources
from app.warehouse.config import require_enabled
from app.warehouse.accepted_changes import manifest, projection, digest
from app.warehouse.source_evidence import extract
from app.warehouse.reliability import (Decision, ProtocolError, Receipt, Result,
                                       application_token, reconcile, recover)


class ReliabilityService:
    def __init__(self, workspace_id, model_id, version_id, user):
        self.scope = (workspace_id, model_id, version_id)
        self.user = user

    @contextmanager
    def transaction(self):
        settings = require_enabled()
        w, m, v = self.scope
        with repository_transaction() as conn:
            _, version = owned_version(conn, w, m, v, self.user, True)
            if version['status'] != 'APPROVED':
                raise ProtocolError(Decision.MODEL_DRIFT)
            sources = validate_sources(conn, w, v, self.user['owner_key'])
            # V1 has only a primary-source typed modeling contract. Do not guess
            # dependency projections or publish independently mixed revisions.
            if len(sources) != 1 or sources[0]['purpose'] != 'PRIMARY':
                raise ValueError('V1 reliability requires one completely pinned PRIMARY source')
            config = metadata.modeling(conn, w, m, v)
            if not config:
                raise ProtocolError(Decision.MODEL_DRIFT)
            yield conn, sources[0], config, settings

    def _operation(self, conn, operation_id, settings):
        row = metadata.operation(conn, self.scope[0], operation_id)
        if not row or (row['workspace_id'], row['warehouse_model_id'], row['warehouse_model_version_id']) != self.scope:
            raise HTTPException(404, 'Operation not found in model version')
        intent = db.intent(conn, operation_id)
        if not intent:
            raise ValueError('Operation has no execution intent')
        resource = metadata.resource(conn, *self.scope, row['resource_id'])
        if (not resource or resource['status'] != 'REGISTERED' or resource['generation'] != intent['generation']
                or row['environment'] != settings.environment):
            raise ProtocolError(Decision.RESOURCE_DRIFT)
        return row, intent

    def discover(self):
        """Explicit read-only candidate discovery; never called at startup.

        Walk the CURRENT published predecessor chain. Candidate version numbers
        may have gaps from failed applications; they are never our positions.
        """
        with self.transaction() as (conn, source, config, settings):
            return self._missing(conn, source)

    def _missing(self, conn, source):
        chain = db.all_rows(conn, '''WITH RECURSIVE chain AS (
            SELECT s.* FROM datasets d JOIN dataset_state_versions s ON s.state_id=d.current_state_id WHERE d.dataset_id=%s
            UNION ALL SELECT s.* FROM dataset_state_versions s JOIN chain c ON s.state_id=c.previous_state_id)
            SELECT c.state_id,c.previous_state_id,c.source_application_id FROM chain c
            JOIN delivery_applications a ON a.application_id=c.source_application_id
            WHERE c.status='PUBLISHED' AND a.status='SUCCESS' AND a.result_state_id=c.state_id ORDER BY c.state_version''',
            (source['source_dataset_id'],))
        known = {r['source_state_id'] for r in db.manifests(conn, source['source_mapping_id'])}
        return [r for r in chain if r['state_id'] not in known]

    def register(self, state_id, store):
        """Separate from source commit: failure is rediscovered by discover().

        Mapping lock serializes position allocation. Manifest + events + outbox
        are one commit; artifact verification must finish before insertion.
        """
        with self.transaction() as (conn, source, config, settings):
            mapping = source['source_mapping_id']
            conn.execute('SELECT source_mapping_id FROM warehouse_model_sources WHERE source_mapping_id=%s FOR UPDATE', (mapping,))
            state = db.one(conn, 'SELECT * FROM dataset_state_versions WHERE state_id=%s AND dataset_id=%s', (state_id, source['source_dataset_id']))
            if not state:
                raise HTTPException(404, 'Source state not found')
            known = db.manifests(conn, mapping)
            old = next((r for r in known if r['source_state_id'] == state_id), None)
            if not old:
                missing = self._missing(conn, source)
                if not missing or missing[0]['state_id'] != state_id:
                    raise ProtocolError(Decision.SOURCE_GAP)
            app = db.one(conn, 'SELECT * FROM delivery_applications WHERE application_id=%s', (state['source_application_id'],))
            policy = metadata.source_contract(conn, app['dataset_id'], app['policy_id'], app['applied_rule_version'])
            if (not policy or app['dataset_version_id'] != config['source_dataset_version_id']
                    or app['policy_id'] != config['source_policy_id'] or app['applied_rule_version'] != config['source_rule_version']):
                raise ProtocolError(Decision.SOURCE_DRIFT)
            # source_contract includes rules for validation; delivery's immutable
            # policy snapshot contains only dataset_load_policies columns.
            rules = policy.pop('rules')
            from app.services.warehouse_modeling_service import rule_digest, ADAPTER
            rule_hash = rule_digest(rules)
            if rule_hash != config['source_rule_sha256']:
                raise ProtocolError(Decision.SOURCE_DRIFT)
            delivery = db.one(conn, 'SELECT * FROM delivery_manifests WHERE application_id=%s', (app['application_id'],))
            if not delivery:
                raise ProtocolError(Decision.SOURCE_DRIFT)
            previous = db.one(conn, '''SELECT s.*,a.snapshot_boundary_at FROM dataset_state_versions s
                JOIN delivery_applications a ON a.application_id=s.source_application_id WHERE s.state_id=%s''', (state['previous_state_id'],))
            typed = ADAPTER.validate_python(config['configuration'])
            pin, events = extract(store, state, app, policy, delivery, previous, projection(typed), self.scope[0], rule_hash)
            body = manifest(pin, mapping, self.scope[2], digest(config['configuration']), events,
                            old['position'] if old else len(known) + 1)
            if old:
                if body != old['body']:
                    raise ProtocolError(Decision.SOURCE_DRIFT)
                return old['body']
            db.register(conn, self.scope, mapping, source['source_dataset_id'], body, known[-1]['manifest_id'] if known else None)
            return body

    def create_intent(self, resource_id, kind, manifest_ids, request_key, *, expected, historical_range=None):
        if kind not in ('INITIAL_LOAD', 'INCREMENTAL', 'REPLAY', 'BACKFILL', 'REBUILD'):
            raise ValueError('Unknown operation kind')
        if not isinstance(expected, Result) or not manifest_ids or len(manifest_ids) > 1000 or len(set(manifest_ids)) != len(manifest_ids):
            raise ValueError('Explicit bounded evidence and expected logical result required')
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 120:
            raise ValueError('Explicit bounded intent idempotency key required')
        if kind == 'BACKFILL' and (not isinstance(historical_range, list) or len(historical_range) != 2
                                  or any(type(x) is not int or x < 1 for x in historical_range)
                                  or historical_range[0] > historical_range[1]):
            raise ValueError('Explicit ordered historical range required')
        with self.transaction() as (conn, source, config, settings):
            resource = metadata.resource(conn, *self.scope, resource_id)
            if not resource or resource['status'] != 'REGISTERED' or resource['environment'] != settings.environment:
                raise ProtocolError(Decision.RESOURCE_DRIFT)
            dispatch_key = digest(['datarise-dispatch', 1, list(self.scope), resource_id, resource['generation'],
                                   kind, sorted(manifest_ids), request_key if kind in ('REPLAY','BACKFILL') else None])
            old = db.one(conn, '''SELECT * FROM warehouse_execution_intents WHERE workspace_id=%s AND warehouse_model_id=%s
                AND warehouse_model_version_id=%s AND resource_id=%s AND (request_key=%s OR dispatch_key=%s)''',
                (*self.scope, resource_id, request_key, dispatch_key))
            if old:
                b = old['body']
                if (set(manifest_ids) != {x['id'] for x in b['manifests']} or b['kind'] != kind
                        or b['expected'] != expected.model_dump(mode='json') or b['historical_range'] != historical_range):
                    raise ValueError('Intent idempotency conflict')
                return old
            rows = db.all_rows(conn, '''SELECT m.* FROM warehouse_accepted_manifests m JOIN warehouse_outbox o USING(manifest_id)
                WHERE m.workspace_id=%s AND m.source_mapping_id=%s AND m.manifest_id=ANY(%s) ORDER BY m.position''',
                (self.scope[0], source['source_mapping_id'], list(manifest_ids)))
            if len(rows) != len(manifest_ids):
                raise ProtocolError(Decision.SOURCE_DRIFT)
            if any(r['body']['configuration_sha256'] != digest(config['configuration']) for r in rows):
                raise ProtocolError(Decision.MODEL_DRIFT)
            positions = [r['position'] for r in rows]
            if kind == 'BACKFILL' and historical_range != [positions[0], positions[-1]]:
                raise ValueError('Backfill bounds must match the pinned source publication range')
            if expected.manifests != len(rows) or expected.events != sum(r['body']['event_count'] for r in rows):
                raise ValueError('Expected result must cover every pinned manifest/event')
            if positions != list(range(positions[0], positions[-1] + 1)):
                raise ProtocolError(Decision.SOURCE_GAP)
            cp = db.checkpoint(conn, resource_id, source['source_mapping_id'])
            cp_position = cp['position'] if cp else 0
            if kind == 'REBUILD' and cp_position != 0:
                raise ValueError('Rebuild requires an unconsumed resource generation')
            if kind == 'REPLAY' and positions[-1] > cp_position:
                raise ProtocolError(Decision.SOURCE_GAP)
            if kind in ('INITIAL_LOAD', 'INCREMENTAL') and positions[0] != cp_position + 1:
                raise ProtocolError(Decision.SOURCE_GAP)
            if kind in ('INITIAL_LOAD', 'REBUILD') and positions[0] != 1:
                raise ProtocolError(Decision.SOURCE_GAP)
            head = db.head(conn, resource_id)
            body = dict(manifests=[dict(id=r['manifest_id'], checksum=r['checksum'], position=r['position']) for r in rows],
                        source_mapping_id=source['source_mapping_id'], source_dataset_id=source['source_dataset_id'],
                        configuration_sha256=digest(config['configuration']), expected=expected.model_dump(mode='json'),
                        prior_position=cp_position, prior_checkpoint_version=cp['version'] if cp else 0,
                        prior_head=head['publication_id'] if head else None, prior_revision=head['revision'] if head else 0,
                        historical_range=historical_range, kind=kind)
            token = application_token([*self.scope, resource_id, resource['generation']], kind, body['manifests'], request_key)
            checksum = digest(['datarise-execution-intent', 1, body, token])
            op = metadata.create_operation(conn, *self.scope, settings.environment, self.user['user_id'],
                                           OperationCreate(resource_id=resource_id, operation_type=kind, reason='Pinned reliability intent: ' + request_key))
            return db.one(conn, '''INSERT INTO warehouse_execution_intents(operation_id,workspace_id,warehouse_model_id,
                warehouse_model_version_id,resource_id,generation,request_key,application_token,dispatch_key,intent_sha256,body)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (op['operation_id'], *self.scope, resource_id, resource['generation'], request_key, token, dispatch_key, checksum, Jsonb(body)))

    def claim(self, operation_id, worker, *, lease_seconds=60):
        if not isinstance(worker, str) or not 1 <= len(worker) <= 120 or not 1 <= lease_seconds <= 900:
            raise ValueError('Bounded worker and lease duration required')
        with self.transaction() as (conn, source, config, settings):
            op, intent = self._operation(conn, operation_id, settings)
            if db.one(conn, 'SELECT 1 FROM warehouse_publications WHERE operation_id=%s', (operation_id,)):
                raise ValueError('Completed operation cannot be retried')
            blocked = db.one(conn, '''SELECT evidence FROM warehouse_run_journal j JOIN warehouse_runs r USING(warehouse_run_id)
                WHERE r.operation_id=%s AND j.stage='BLOCKED' ORDER BY j.journal_id DESC LIMIT 1''', (operation_id,))
            if blocked and blocked['evidence'].get('decision') == Decision.MANUAL_REVIEW:
                raise ProtocolError(Decision.MANUAL_REVIEW)
            # The model lock serializes claims, including the initial absent lease.
            old = db.lease(conn, intent['resource_id'])
            if old and old['active']:
                raise ProtocolError(Decision.FENCED)
            if old and old['operation_id'] != operation_id:
                # Expiry is permission to recover the prior intent, not proof
                # that its external effects vanished. A different token must
                # not bypass an unknown or applied-but-unpublished operation.
                prior_block = db.one(conn, '''SELECT j.evidence FROM warehouse_run_journal j
                    JOIN warehouse_runs r USING(warehouse_run_id) WHERE r.operation_id=%s AND j.stage='BLOCKED'
                    ORDER BY j.journal_id DESC LIMIT 1''', (old['operation_id'],))
                if prior_block and prior_block['evidence'].get('decision') == Decision.MANUAL_REVIEW:
                    raise ProtocolError(Decision.MANUAL_REVIEW)
                prior_stage = db.latest_stage(conn, old['operation_id'])
                published = db.one(conn, 'SELECT 1 FROM warehouse_publications WHERE operation_id=%s', (old['operation_id'],))
                if not published and prior_stage and (prior_stage['stage'] == 'PLANNED'
                        or prior_stage['evidence'].get('outcome') in ('SUCCEEDED', 'UNKNOWN')):
                    raise ProtocolError(Decision.RECONCILE_FIRST)
            if old:
                run = metadata.run(conn, self.scope[0], old['operation_id'], old['warehouse_run_id'])
                if run['status'] in ('PENDING', 'RUNNING'):
                    metadata.transition(conn, run['warehouse_run_id'], RunTransition(status='FAILED', failure_code='LEASE_EXPIRED'))
            run = metadata.new_attempt(conn, self.scope[0], operation_id, self.user['user_id'])
            metadata.transition(conn, run['warehouse_run_id'], RunTransition(status='RUNNING'))
            fence = old['fence'] + 1 if old else 1
            conn.execute('''INSERT INTO warehouse_writer_leases(resource_id,operation_id,warehouse_run_id,fence,worker,expires_at)
                VALUES (%s,%s,%s,%s,%s,clock_timestamp()+%s) ON CONFLICT(resource_id) DO UPDATE SET
                operation_id=EXCLUDED.operation_id,warehouse_run_id=EXCLUDED.warehouse_run_id,fence=EXCLUDED.fence,
                worker=EXCLUDED.worker,expires_at=EXCLUDED.expires_at''',
                (intent['resource_id'], operation_id, run['warehouse_run_id'], fence, worker, timedelta(seconds=lease_seconds)))
            db.journal(conn, run['warehouse_run_id'], fence, 'CLAIMED', {})
            last = db.latest_stage(conn, operation_id)
            decision = Decision.RETRY_SAFE if not last else recover(Receipt.model_validate(last['evidence']) if last['stage'] == 'TARGET_RECEIPT' else None)
            return dict(run_id=run['warehouse_run_id'], fence=fence, decision=decision)

    def _writer(self, conn, operation_id, run_id, fence, settings):
        op, intent = self._operation(conn, operation_id, settings)
        lease = db.lease(conn, intent['resource_id'])
        run = metadata.run(conn, self.scope[0], operation_id, run_id)
        if (not lease or not lease['active'] or lease['operation_id'] != operation_id
                or lease['warehouse_run_id'] != run_id or lease['fence'] != fence or not run or run['status'] != 'RUNNING'):
            raise ProtocolError(Decision.FENCED)
        return op, intent

    def plan(self, operation_id, run_id, fence):
        with self.transaction() as (conn, source, config, settings):
            op, intent = self._writer(conn, operation_id, run_id, fence, settings)
            last = db.latest_stage(conn, operation_id)
            decision = Decision.RETRY_SAFE if not last else recover(Receipt.model_validate(last['evidence']) if last['stage'] == 'TARGET_RECEIPT' else None)
            if decision != Decision.RETRY_SAFE:
                raise ProtocolError(decision)
            if intent['body']['configuration_sha256'] != digest(config['configuration']):
                raise ProtocolError(Decision.MODEL_DRIFT)
            b = intent['body']
            h = db.head(conn, intent['resource_id'])
            cp = db.checkpoint(conn, intent['resource_id'], source['source_mapping_id'])
            if ((h['publication_id'] if h else None) != b['prior_head']
                    or (cp['position'] if cp else 0) != b['prior_position']
                    or (cp['version'] if cp else 0) != b['prior_checkpoint_version']):
                raise ProtocolError(Decision.SOURCE_GAP)
            db.journal(conn, run_id, fence, 'PLANNED', dict(application_token=intent['application_token']))
            return intent

    def record_receipt(self, operation_id, run_id, fence, receipt):
        if not isinstance(receipt, Receipt):
            raise ValueError('Typed receipt required')
        with self.transaction() as (conn, source, config, settings):
            op, intent = self._writer(conn, operation_id, run_id, fence, settings)
            if (receipt.run_id != run_id or receipt.operation_id != operation_id or receipt.application_token != intent['application_token']
                    or receipt.intent_sha256 != intent['intent_sha256'] or receipt.resource_generation != intent['generation']):
                raise ProtocolError(Decision.RESOURCE_DRIFT)
            last = db.latest_stage(conn, operation_id)
            if not last:
                raise ValueError('Plan or prior uncertain execution required')
            db.journal(conn, run_id, fence, 'TARGET_RECEIPT', receipt.model_dump(mode='json'))
            if receipt.outcome == 'FAILED':
                metadata.transition(conn, run_id, RunTransition(status='FAILED', failure_code='TARGET_FAILED'))
                conn.execute('UPDATE warehouse_writer_leases SET expires_at=clock_timestamp() WHERE resource_id=%s', (intent['resource_id'],))
            return recover(receipt)

    def reconcile(self, operation_id, run_id, fence):
        with self.transaction() as (conn, source, config, settings):
            op, intent = self._writer(conn, operation_id, run_id, fence, settings)
            last = db.latest_stage(conn, operation_id)
            if not last or last['stage'] != 'TARGET_RECEIPT':
                raise ProtocolError(Decision.RECONCILE_FIRST)
            receipt = Receipt.model_validate(last['evidence'])
            passed = reconcile(Result.model_validate(intent['body']['expected']), receipt, intent, operation_id)
            row = db.one(conn, '''INSERT INTO warehouse_reconciliations(operation_id,warehouse_run_id,fence,passed,evidence)
                VALUES (%s,%s,%s,%s,%s) RETURNING *''', (operation_id, run_id, fence, passed,
                Jsonb(dict(receipt=receipt.model_dump(mode='json'), expected=intent['body']['expected']))))
            db.journal(conn, run_id, fence, 'RECONCILED' if passed else 'BLOCKED', dict(reconciliation_id=row['reconciliation_id'], passed=passed))
            return row

    def publish(self, operation_id, run_id, fence, reconciliation_id):
        with self.transaction() as (conn, source, config, settings):
            op, intent = self._writer(conn, operation_id, run_id, fence, settings)
            r = db.one(conn, '''SELECT * FROM warehouse_reconciliations WHERE reconciliation_id=%s AND operation_id=%s
                AND warehouse_run_id=%s AND fence=%s AND passed''', (reconciliation_id, operation_id, run_id, fence))
            if not r:
                raise ValueError('Successful current fenced reconciliation required')
            latest = db.one(conn, '''SELECT reconciliation_id,passed FROM warehouse_reconciliations
                WHERE operation_id=%s ORDER BY reconciliation_id DESC LIMIT 1''', (operation_id,))
            last_receipt = db.latest_stage(conn, operation_id)
            if (not latest or latest['reconciliation_id'] != reconciliation_id or not latest['passed']
                    or not last_receipt or last_receipt['stage'] != 'TARGET_RECEIPT'
                    or last_receipt['evidence'] != r['evidence']['receipt']):
                raise ValueError('Reconciliation superseded by later target evidence')
            b = intent['body']
            resource = intent['resource_id']
            cp = db.checkpoint(conn, resource, source['source_mapping_id'])
            h = db.head(conn, resource)
            if ((h['publication_id'] if h else None) != b['prior_head']
                    or (cp['position'] if cp else 0) != b['prior_position']
                    or (cp['version'] if cp else 0) != b['prior_checkpoint_version']):
                raise ProtocolError(Decision.SOURCE_GAP)
            # Replay/backfill publish explicit provenance without changing normal
            # progress. REBUILD starts at 1, may only advance a fresh generation.
            advance = op['operation_type'] in ('INITIAL_LOAD', 'INCREMENTAL', 'REBUILD')
            positions = [x['position'] for x in b['manifests']]
            if advance and (positions[0] != b['prior_position'] + 1 or positions != list(range(positions[0], positions[-1] + 1))):
                raise ProtocolError(Decision.SOURCE_GAP)
            position = positions[-1] if advance else b['prior_position']
            normal = db.one(conn, 'SELECT manifest_id,body FROM warehouse_accepted_manifests WHERE source_mapping_id=%s AND position=%s',
                            (source['source_mapping_id'], position))
            vector = [dict(mapping_id=source['source_mapping_id'], dataset_id=source['source_dataset_id'], position=position,
                           manifest_id=normal['manifest_id'] if normal else None, pin=normal['body']['pin'] if normal else None,
                           processed_manifests=b['manifests'], mode=op['operation_type'], historical_range=b['historical_range'])]
            p = db.one(conn, '''INSERT INTO warehouse_publications(operation_id,warehouse_run_id,reconciliation_id,resource_id,
                revision,previous_publication_id,source_vector) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (operation_id, run_id, reconciliation_id, resource, b['prior_revision'] + 1, b['prior_head'], Jsonb(vector)))
            if h:
                changed = conn.execute('''UPDATE warehouse_publication_heads SET publication_id=%s,revision=revision+1
                    WHERE resource_id=%s AND publication_id=%s AND revision=%s''',
                    (p['publication_id'], resource, b['prior_head'], b['prior_revision'])).rowcount
                if changed != 1:
                    raise ProtocolError(Decision.SOURCE_GAP)
            else:
                conn.execute('INSERT INTO warehouse_publication_heads VALUES (%s,%s,%s)', (resource, p['publication_id'], p['revision']))
            if cp:
                changed = conn.execute('''UPDATE warehouse_checkpoints SET position=%s,version=version+1,publication_id=%s
                    WHERE resource_id=%s AND source_mapping_id=%s AND position=%s AND version=%s''',
                    (position, p['publication_id'], resource, source['source_mapping_id'], b['prior_position'], b['prior_checkpoint_version'])).rowcount
                if changed != 1:
                    raise ProtocolError(Decision.SOURCE_GAP)
            else:
                conn.execute('INSERT INTO warehouse_checkpoints VALUES (%s,%s,%s,1,%s)', (resource, source['source_mapping_id'], position, p['publication_id']))
            db.journal(conn, run_id, fence, 'PUBLISHED', dict(publication_id=p['publication_id']))
            metadata.transition(conn, run_id, RunTransition(status='SUCCEEDED'))
            conn.execute('UPDATE warehouse_writer_leases SET expires_at=clock_timestamp() WHERE resource_id=%s', (resource,))
            return p

    def read_progress(self, resource_id):
        with self.transaction() as (conn, source, config, settings):
            if not metadata.resource(conn, *self.scope, resource_id):
                raise HTTPException(404, 'Resource not found')
            return dict(head=db.head(conn, resource_id), checkpoint=db.checkpoint(conn, resource_id, source['source_mapping_id']))

    def pending(self, resource_id, *, limit=100):
        """Recover registered outbox work even if a crash preceded dispatch.

        Existing incomplete intents can be reconstructed idempotently from these
        same manifests; normal progress is the only acknowledgement of consumption.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Bounded outbox page required')
        with self.transaction() as (conn, source, config, settings):
            if not metadata.resource(conn, *self.scope, resource_id):
                raise HTTPException(404, 'Resource not found')
            cp = db.checkpoint(conn, resource_id, source['source_mapping_id'])
            rows = db.all_rows(conn, '''SELECT m.body FROM warehouse_outbox o JOIN warehouse_accepted_manifests m USING(manifest_id)
                WHERE m.source_mapping_id=%s AND m.position>%s ORDER BY m.position LIMIT %s''',
                (source['source_mapping_id'], cp['position'] if cp else 0, limit))
            return [r['body'] for r in rows]

    def manual_review(self, operation_id, run_id, fence):
        """Fail closed when token evidence cannot determine the target outcome."""
        with self.transaction() as (conn, source, config, settings):
            self._writer(conn, operation_id, run_id, fence, settings)
            db.journal(conn, run_id, fence, 'BLOCKED', dict(decision=Decision.MANUAL_REVIEW.value))
            metadata.transition(conn, run_id, RunTransition(status='FAILED', failure_code='TARGET_UNCERTAIN'))
