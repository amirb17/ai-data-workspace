"""Reference-model tests, NOT proof of PostgreSQL/S3 atomicity or live recovery."""
import hashlib
import json
from uuid import uuid4

import pytest

from app.processing_worker.artifact_contract import ArtifactVerificationError, verify_candidate


def digest(body):
    return hashlib.sha256(body).hexdigest()


class Store:
    """In-memory write-once objects; failure may be reported after a write."""
    def __init__(self):
        self.objects = {}
    def put(self, key, payload, *, fail_after_write=False):
        if key in self.objects:
            raise FileExistsError('Candidate object already exists')
        self.objects[key] = payload
        if fail_after_write:
            raise ConnectionError('Ambiguous write response')
    def read(self, key):
        return self.objects[key]


class PublicationModel:
    """Executable DESIGN model: its atomic methods still need real SQL implementation.

    Fake time and token represent PostgreSQL time/row fencing. No SDK, real
    database, processing attempts, worker execution or actual cleanup is used.
    """
    def __init__(self, stage='SILVER'):
        self.stage = stage
        self.roles = frozenset({'BRONZE': ('data', 'lexical'), 'SILVER': ('data', 'quarantine'),
                                'GOLD': ('base', 'mart_orders')}[stage])
        self.intent, self.candidate = uuid4(), uuid4()
        self.fingerprint = digest(b'trusted source/schema/rule/planner pins')
        self.token = uuid4()
        self.writer_token = self.token
        self.now, self.expires = 0, 10
        self.candidate_state = 'ACTIVE'
        self.store = Store()
        self.descriptors = []
        self.receipt = None
        self.pointer = 'legacy-publication'
        self.job_complete = False
        self.references = set()
        self.manifest_bytes = None

    @property
    def prefix(self):
        return f'artifact-candidates/{self.candidate}/'

    def write(self, role, *, fail_after_write=False):
        body = ('synthetic bytes for '+role).encode()
        key = self.prefix+role+'.parquet'
        self.store.put(key, body, fail_after_write=fail_after_write)
        self.descriptors.append({'role': role, 'key': key, 'sha256': digest(body), 'size_bytes': len(body)})

    def encode(self):
        return json.dumps({'version': 1, 'intent_id': str(self.intent), 'candidate_id': str(self.candidate),
            'stage': self.stage, 'input_fingerprint': self.fingerprint, 'artifacts': self.descriptors},
            sort_keys=True, separators=(',', ':')).encode()

    def write_all(self):
        for role in sorted(self.roles):
            self.write(role)
        self.manifest_bytes = self.encode()
        self.store.put(self.prefix+'manifest.json', self.manifest_bytes)

    def verify(self, payload=None, **overrides):
        payload = self.manifest_bytes if payload is None else payload
        pins = dict(manifest_sha256=digest(payload), intent_id=self.intent, candidate_id=self.candidate,
            stage=self.stage, input_fingerprint=self.fingerprint, expected_roles=self.roles,
            read_artifact=self.store.read, max_artifact_bytes=1024)
        pins.update(overrides)
        return verify_candidate(payload, **pins)

    def check_owner(self, token):
        if token != self.token or self.now >= self.expires:
            raise PermissionError('Execution token is not current and live')

    def reclaim(self):
        assert self.now >= self.expires
        self.token = uuid4()
        self.expires = self.now+10
        return self.token

    def publish(self, token, *, fail_commit=False):
        verified = self.verify()
        # Models one SQL transaction: job/candidate locks, ownership/input check,
        # receipt uniqueness, all metadata and pointer change, then commit.
        self.check_owner(token)
        if self.candidate_state == 'RETIRED':
            raise PermissionError('Retired candidates can never publish')
        if self.receipt is not None:
            return self.receipt
        if fail_commit:
            raise RuntimeError('Database commit failed')
        self.receipt = verified
        self.pointer = verified.manifest_sha256
        self.candidate_state = 'COMMITTED'
        self.references.update(self.store.objects)
        return verified

    def recover_completed(self, token):
        self.check_owner(token)
        if self.receipt is None:
            return False
        assert self.verify() == self.receipt
        self.job_complete = True
        return True

    def retire(self, *, grace_elapsed):
        # Models a shared candidate-row lock/CAS with publish. Never just list+delete.
        if (not grace_elapsed or self.candidate_state == 'COMMITTED' or self.references
            or (self.writer_token == self.token and self.now < self.expires)):
            return False
        self.candidate_state = 'RETIRED'
        return True

    def sweep_retired(self):
        assert self.candidate_state == 'RETIRED'
        for key in list(self.store.objects):
            if key.startswith(self.prefix):
                del self.store.objects[key]  # In-memory test data only.


@pytest.mark.parametrize('stage', ['BRONZE', 'SILVER', 'GOLD'])
def test_complete_candidate_publishes_as_one_receipt(stage):
    model = PublicationModel(stage)
    model.write_all()
    receipt = model.publish(model.token)
    assert receipt.artifact_count == 2 and receipt.stage == stage
    assert model.pointer == receipt.manifest_sha256 and model.candidate_state == 'COMMITTED'


def test_crash_before_artifacts_does_not_publish_or_ack():
    model = PublicationModel()
    assert not model.store.objects and model.pointer == 'legacy-publication'
    with pytest.raises(ArtifactVerificationError, match='incomplete'):
        model.verify(model.encode())
    model.now = 11
    assert not model.recover_completed(model.reclaim())
    assert model.receipt is None and not model.job_complete


@pytest.mark.parametrize('ambiguous_write', [False, True])
def test_crash_during_artifact_set_cannot_publish(ambiguous_write):
    model = PublicationModel()
    if ambiguous_write:
        with pytest.raises(ConnectionError):
            model.write('data', fail_after_write=True)
    else:
        model.write('data')
    with pytest.raises(ArtifactVerificationError, match='incomplete'):
        model.verify(model.encode())
    assert model.receipt is None and model.pointer == 'legacy-publication'


def test_written_objects_survive_db_rollback_without_becoming_visible():
    model = PublicationModel()
    model.write_all()
    objects = dict(model.store.objects)
    with pytest.raises(RuntimeError, match='commit failed'):
        model.publish(model.token, fail_commit=True)
    assert model.store.objects == objects and model.receipt is None
    assert model.pointer == 'legacy-publication' and not model.job_complete
    model.now = 11
    model.publish(model.reclaim())  # Verified immutable adoption; no storage overwrite.
    assert model.store.objects == objects


def test_commit_then_crash_before_ack_recovers_without_rewriting():
    model = PublicationModel()
    model.write_all()
    receipt = model.publish(model.token)
    objects = dict(model.store.objects)
    assert not model.job_complete
    model.now = 11
    assert model.recover_completed(model.reclaim())
    assert model.receipt == receipt and model.store.objects == objects and model.job_complete


def test_duplicate_publication_and_retry_reuse_the_receipt():
    model = PublicationModel()
    model.write_all()
    first = model.publish(model.token)
    assert model.publish(model.token) is first
    assert model.recover_completed(model.token)
    with pytest.raises(FileExistsError):
        model.write('data')


def test_lease_expiry_during_storage_blocks_publication():
    model = PublicationModel()
    model.write('data')
    model.now = 11
    model.write('quarantine')  # A stale writer may finish I/O; visibility stays fenced.
    model.manifest_bytes = model.encode()
    with pytest.raises(PermissionError):
        model.publish(model.token)
    assert model.pointer == 'legacy-publication' and model.receipt is None


def test_old_worker_cannot_publish_after_new_worker_claims():
    model = PublicationModel()
    old = model.token
    model.write_all()
    model.now = 11
    current = model.reclaim()
    with pytest.raises(PermissionError):
        model.publish(old)
    current_receipt = model.publish(current)
    with pytest.raises(PermissionError):
        model.publish(old)
    assert model.receipt == current_receipt


@pytest.mark.parametrize('corruption', ['bytes', 'truncated_bytes', 'missing_object', 'manifest_checksum', 'missing_role',
    'duplicate_role', 'outside_scope', 'wrong_input', 'size', 'unsupported_version', 'duplicate_field'])
def test_bad_or_incomplete_evidence_never_verifies(corruption):
    model = PublicationModel()
    model.write_all()
    payload = model.manifest_bytes
    overrides = {}
    if corruption == 'bytes':
        key = model.descriptors[0]['key']
        model.store.objects[key] = b'x'*len(model.store.objects[key])
    elif corruption == 'truncated_bytes':
        key = model.descriptors[0]['key']
        model.store.objects[key] = model.store.objects[key][:-1]
    elif corruption == 'missing_object':
        del model.store.objects[model.descriptors[0]['key']]
    elif corruption == 'manifest_checksum':
        overrides['manifest_sha256'] = '0'*64
    elif corruption == 'missing_role':
        model.descriptors.pop()
        payload = model.encode()
    elif corruption == 'duplicate_role':
        model.descriptors[1]['role'] = model.descriptors[0]['role']
        payload = model.encode()
    elif corruption == 'outside_scope':
        model.descriptors[0]['key'] = 'legacy/data.parquet'
        payload = model.encode()
    elif corruption == 'wrong_input':
        overrides['input_fingerprint'] = '0'*64
    elif corruption == 'size':
        model.descriptors[0]['size_bytes'] += 1
        payload = model.encode()
    elif corruption == 'unsupported_version':
        payload = payload.replace(b'"version":1', b'"version":2')
    else:
        payload = payload.replace(b'"version":1', b'"version":1,"version":1')
    with pytest.raises((ArtifactVerificationError, KeyError)):
        model.verify(payload, **overrides)
    assert model.pointer == 'legacy-publication' and model.receipt is None


def test_cleanup_skips_live_referenced_young_and_published_candidates():
    model = PublicationModel()
    model.write_all()
    assert not model.retire(grace_elapsed=True)  # Live writer.
    model.now = 11
    assert not model.retire(grace_elapsed=False)
    model.references.add('historical-or-downstream-reference')
    assert not model.retire(grace_elapsed=True)
    model.references.clear()
    model.publish(model.reclaim())
    assert not model.retire(grace_elapsed=True)


def test_retirement_prevents_later_publication_and_repeated_sweep_is_safe():
    model = PublicationModel()
    model.write_all()
    model.now = 11
    token = model.reclaim()
    assert model.retire(grace_elapsed=True)
    with pytest.raises(PermissionError):
        model.publish(token)
    model.store.objects['legacy/historical.parquet'] = b'untouched'
    model.sweep_retired()
    model.store.put(model.prefix+'late-orphan.parquet', b'late write')
    model.sweep_retired()  # Tombstone remains: late writes cannot resurrect publication.
    assert model.store.objects == {'legacy/historical.parquet': b'untouched'}
