"""Opt-in durable adapter over the EXISTING processing services and algorithms.

Clients/verifier are injected. No SDK, credentials, transport or live execution
is constructed/enabled here. A deployment bootstrap must enforce its gates.
"""
import io
import json
from contextlib import contextmanager
from dataclasses import asdict
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db import database, artifact_publication_repository as publication
from app.db import processing_integration_repository as registry
from app.processing_worker import io_context
from app.storage.versioned_artifacts import ObjectIdentity, validate_csv_source


class StageIO:
    def __init__(self, boundary, storage, verifier, contract, prior):
        self.boundary, self.storage, self.verifier = boundary, storage, verifier
        self.contract, self.prior = contract, prior
        self.stage = contract['stage']
        self.intent_id, self.candidate_id = uuid4(), uuid4()
        self.objects, self.aliases = {}, {}
        self.receipt = None
        self.attempt_id = None
        self.input_objects = {}
        if self.stage == 'BRONZE':
            source = ObjectIdentity.decode(contract['source'])
            self.input_objects[source.key] = source
        else:
            upstream = {r: ObjectIdentity.decode(o) for r,o in contract['upstream_artifacts'].items()}
            self.input_objects.update({o.key:o for o in upstream.values()})
            if self.stage == 'SILVER':
                for role, filename in [('data','data.parquet'), ('lexical','raw_text.parquet')]:
                    self.input_objects[f"bronze/file_id={contract['file_id']}/{filename}"] = upstream[role]
                if contract.get('raw_source'):
                    raw = ObjectIdentity.decode(contract['raw_source'])
                    self.input_objects[raw.key] = raw

    def get_object(self, *, Bucket, Key):
        identity = self.input_objects.get(Key) or self.aliases.get(Key)
        if identity is None:
            raise ValueError('Processor requested an unregistered source')
        payload = self.storage.read(identity)
        return {'Body': io.BytesIO(payload), 'ContentLength': len(payload), 'VersionId': identity.version}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        if self.stage == 'BRONZE':
            role = 'lexical' if Key.endswith('/raw_text.parquet') else 'data'
        elif self.stage == 'SILVER':
            role = 'quarantine' if Key.startswith('quarantine/') else 'data'
        else:
            role = 'mart_'+Key.split('/')[2] if Key.startswith('gold/marts/') else 'base'
        if role in self.objects or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in role):
            raise ValueError('Invalid or repeated artifact role')
        key = f'artifact-candidates/{self.candidate_id}/{role}.parquet'
        identity = self.storage.put(key, Body)
        self.objects[role] = identity
        self.aliases[Key] = identity
        self.aliases[key] = identity
        return {'VersionId': identity.version}

    def translate(self, value):
        if isinstance(value, str) and value in self.aliases:
            return self.aliases[value].key
        if isinstance(value, dict):
            return {k:self.translate(v) for k,v in value.items()}
        if isinstance(value, list):
            return [self.translate(v) for v in value]
        return value

    @contextmanager
    def failure(self):
        with database.repository_transaction() as conn:
            conn.execute('SELECT task20_require_job(%s,%s)', (self.boundary.lease.job_id, self.boundary.lease.lease_token))
            if self.attempt_id is not None:
                status = conn.execute('SELECT status FROM processing_attempts WHERE attempt_id=%s FOR UPDATE', (self.attempt_id,)).fetchone()
                if not status or status[0] != 'PROCESSING':
                    raise PermissionError('Stage attempt no longer owns execution')
            yield conn
            conn.execute('SELECT task20_require_job(%s,%s)', (self.boundary.lease.job_id, self.boundary.lease.lease_token))

    def attempt_created(self, attempt_id):
        lease = self.boundary.lease
        with database.get_connection() as conn:
            conn.execute('SELECT task20_require_job(%s,%s)', (lease.job_id,lease.lease_token))
            conn.execute('INSERT INTO processing_job_stage_attempts(job_id,lease_token,stage,attempt_id) VALUES(%s,%s,%s,%s)',
                         (lease.job_id,lease.lease_token,self.stage,attempt_id))
        self.attempt_id = attempt_id

    @contextmanager
    def publication(self):
        lease = self.boundary.lease
        if not self.objects and self.stage == 'GOLD':
            proof = json.dumps({'kind':'EMPTY_VALID_INPUT', 'dq_run':self.contract['dq_run']}, sort_keys=True).encode()
            self.objects['skip'] = self.storage.put(f'artifact-candidates/{self.candidate_id}/skip.json', proof)
        descriptors = [{'role':r, 'key':o.key, 'sha256':o.sha256, 'size_bytes':o.size} for r,o in sorted(self.objects.items())]
        manifest = json.dumps(dict(version=1, intent_id=str(self.intent_id), candidate_id=str(self.candidate_id),
            stage=self.stage, input_fingerprint=registry.fingerprint(self.contract), artifacts=descriptors),
            sort_keys=True, separators=(',', ':')).encode()
        identity = self.storage.put(f'artifact-candidates/{self.candidate_id}/manifest.json', manifest)
        identities = {r:asdict(o) for r,o in self.objects.items()}
        with database.repository_transaction() as conn:
            registry.register(conn, lease, self.contract, self.candidate_id, identity, identities,
                intent_id=self.intent_id, expected_receipt=self.prior['receipt_id'] if self.prior else None)
        self.verifier(self.candidate_id)
        self.boundary.checkpoint(renew=True)
        with database.repository_transaction() as conn:
            conn.execute('SELECT task20_require_input(%s,%s,%s)', (lease.job_id, lease.lease_token, Jsonb(self.contract)))
            if self.attempt_id is not None and conn.execute('SELECT status FROM processing_attempts WHERE attempt_id=%s FOR UPDATE', (self.attempt_id,)).fetchone()[0] != 'PROCESSING':
                raise PermissionError('Interrupted stage cannot publish')
            yield conn  # Existing profile/DQ/catalog/attempt/status writes join THIS transaction.
            self.receipt = conn.execute('SELECT task20_publish(%s,%s,%s)',
                (lease.job_id, lease.lease_token, self.candidate_id)).fetchone()[0]


def reusable_initial_append(source, target):
    """Trust an existing verified Silver result, never a different source/rule.

    Only first-time APPEND activation is permitted. It is checked again against
    live dataset state and under the trusted publication lease before applying.
    """
    if not isinstance(source, dict) or not isinstance(target, dict):
        return False
    policy = target.get('load_policy')
    if (source.get('stage') != 'SILVER' or target.get('stage') != 'SILVER'
        or source.get('load_policy') is not None
        or source.get('snapshot_context') is not None
        or not isinstance(policy, dict)
        or policy.get('load_strategy') != 'APPEND'
        or target.get('snapshot_context') is not None):
        return False
    # No other contract field may change, including upstream receipt, rule
    # version, raw identity, dataset association, and dataset version.
    return {k: v for k, v in source.items()
            if k not in ('load_policy', 'snapshot_context')} == {
                k: v for k, v in target.items()
                if k not in ('load_policy', 'snapshot_context')}


class IntegratedExecutor:
    def __init__(self, storage_factory, verifier, *, enabled=False, isolated_test=False):
        self.storage_factory, self.verifier, self.enabled = storage_factory, verifier, enabled
        self.isolated_test = isolated_test

    @contextmanager
    def _physical_stage(self, boundary):
        """Recover only mapped abandoned Bronze while holding a new file lock.

        Existing dataset/delivery locks remain unchanged. Unmapped legacy
        attempts and live job leases are deliberately left alone.
        """
        with database.get_connection() as lock:
            lock.autocommit = True
            scope = boundary.checkpoint()
            key = f"{lock.execute('SELECT current_schema()').fetchone()[0]}:task20-bronze:{scope.file_id}"
            if not lock.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', (key,)).fetchone()[0]:
                raise RuntimeError('Physical Bronze execution is busy')
            try:
                with database.repository_transaction() as conn:
                    conn.execute('SELECT task20_require_job(%s,%s)', (boundary.lease.job_id,boundary.lease.lease_token))
                    conn.execute("""UPDATE processing_attempts a SET status='CRASHED',
                        completed_at=clock_timestamp(),error_message='Durable worker interrupted'
                        FROM processing_job_stage_attempts l,processing_jobs j
                        WHERE a.attempt_id=l.attempt_id AND l.job_id=j.job_id
                        AND l.stage='BRONZE' AND a.file_id=%s AND a.status='PROCESSING'
                        AND (j.lease_token IS DISTINCT FROM l.lease_token OR j.status<>'RUNNING'
                             OR j.lease_expires_at<=clock_timestamp())""", (scope.file_id,))
                yield
            finally:
                lock.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))

    def _apply(self, boundary, storage, contract, *, source_contract=None):
        if contract['stage'] != 'SILVER' or contract.get('load_policy') is None:
            return
        from app.services.append_application_service import apply_incremental
        from app.processing_worker.incremental_adapter import ApplyGuard
        with database.repository_transaction() as conn:
            job = registry.authorized_job(conn, boundary.lease)
            user = registry.row(conn, 'SELECT user_id,owner_key FROM users WHERE user_id=%s', (job['requested_by'],))
            silver = registry.head(conn, 'SILVER', job['file_id'], contract['association'])
        guard = ApplyGuard(boundary, storage, contract, ObjectIdentity.decode(silver['artifact_identities']['data']), source_contract=source_contract)
        with io_context.using(guard):
            apply_incremental(job['workspace_id'], job['dataset_id'], user, job['upload_id'])
        boundary.checkpoint(renew=True)

    def __call__(self, boundary):
        if not self.enabled:
            raise RuntimeError('Integrated processing is disabled pending deployment approval')
        from app.processing_worker.runtime_security import require_runtime
        with database.get_connection() as conn:
            require_runtime(conn, 'worker', isolated_test=self.isolated_test)
        scope = boundary.checkpoint(renew=True)
        storage = self.storage_factory(lambda: boundary.checkpoint(renew=True))
        lease = boundary.lease
        # Acquire EXISTING dataset/lifecycle/delivery contexts, including existing
        # interrupted-delivery recovery. No processing work is run in Lambda.
        with boundary.stage(required_ms=1000):
            from app.services import processing_service as processing
            with database.repository_transaction() as conn:
                job = registry.authorized_job(conn, lease)
                if not self.isolated_test and not conn.execute('SELECT 1 FROM processing_admission_pins WHERE job_id=%s', (lease.job_id,)).fetchone():
                    raise ValueError('Production stage requires immutable admission pins')
                source = registry.row(conn, 'SELECT identity FROM processing_source_objects WHERE file_id=%s', (job['file_id'],))
                physical = registry.row(conn, 'SELECT * FROM physical_files WHERE file_id=%s', (job['file_id'],))
            source_identity = None
            if job['operation'] == 'BRONZE' and source is None:
                if not self.isolated_test:
                    raise ValueError('Production Raw version must be pinned at upload completion')
                from app.storage.s3_service import parse_s3_uri
                source_identity = storage.pin_source(parse_s3_uri(physical['storage_path']), physical['file_hash'], physical['file_size'])
            if job['operation'] == 'BRONZE':
                # Validate even when ingestion already registered the source version.
                pinned = source_identity or ObjectIdentity.decode(source['identity'])
                validate_csv_source(storage.read(pinned))
            with database.repository_transaction() as conn:
                job, value = registry.contract(conn, lease, source_identity=source_identity)
                prior = registry.head(conn, value['stage'], job['file_id'], value.get('association'))
            # An initial load policy can be approved after an already verified Silver
            # publication. Reuse that immutable Silver output only when every
            # Silver dependency is identical and the new policy is the first
            # APPEND policy for a dataset that has never published a state.
            # A fresh job/lease and task20 admission pins still gate the apply.
            if prior and prior['verification_report'] and reusable_initial_append(prior['input_contract'], value):
                storage.read(ObjectIdentity.decode(prior['manifest_identity']))
                for obj in prior['artifact_identities'].values():
                    storage.read(ObjectIdentity.decode(obj))
                with database.repository_transaction() as conn:
                    conn.execute('SELECT task20_require_input(%s,%s,%s)',
                                 (lease.job_id, lease.lease_token, Jsonb(value)))
                    state = conn.execute('SELECT current_state_id FROM datasets WHERE dataset_id=%s',
                                         (job['dataset_id'],)).fetchone()
                    if not state or state[0] is not None:
                        raise ValueError('Initial APPEND reuse requires an empty accepted dataset')
                self._apply(boundary, storage, value, source_contract=prior['input_contract'])
                return
            if prior and prior['verification_report'] and prior['input_contract'] == value:
                # Completion loss / duplicate new job: do not rewrite objects or rewind a head.
                storage.read(ObjectIdentity.decode(prior['manifest_identity']))
                for obj in prior['artifact_identities'].values():
                    storage.read(ObjectIdentity.decode(obj))
                self._apply(boundary, storage, value)
                return
            if value['stage'] != 'BRONZE':
                with database.repository_transaction() as conn:
                    conn.execute('SELECT task20_require_input(%s,%s,%s)', (lease.job_id, lease.lease_token, Jsonb(value)))
                    state = 'READY_FOR_SILVER' if value['stage'] == 'SILVER' else 'READY_FOR_GOLD'
                    conn.execute("UPDATE dataset_version_files SET status=%s WHERE dataset_version_file_id=%s AND status IN ('SUCCESS','SUCCESS_WITH_WARNINGS')",
                        (state, value['association']))
            if value['stage'] != 'BRONZE' and any(r.get('rule_config',{}).get('mode') == 'EXACT' for r in value['rules']):
                raise ValueError('Exact worker publication requires separate gate approval')
            adapter = StageIO(boundary, storage, self.verifier, value, prior)
            from contextlib import nullcontext
            with (self._physical_stage(boundary) if value['stage']=='BRONZE' else nullcontext()), io_context.using(adapter):
                if value['stage'] == 'BRONZE':
                    processing.start_processing(job['upload_id'])
                elif value['stage'] == 'SILVER':
                    processing.run_silver_processing(value['association'], storage.bucket)
                else:
                    processing.run_gold_processing(value['association'], storage.bucket)
            if adapter.receipt is None:
                raise RuntimeError('Stage returned without durable publication')
            self._apply(boundary, storage, value)
