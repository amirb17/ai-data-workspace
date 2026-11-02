import hashlib
import io
from dataclasses import asdict, replace
from uuid import uuid4

import pytest

from app.storage.versioned_artifacts import MemoryVersionedClient, ObjectIdentity, StorageIntegrityError, VersionedArtifacts


def test_versioned_read_uses_registered_version_after_latest_changes():
    client = MemoryVersionedClient()
    original = client.seed('raw/source.csv', b'original')
    client.seed('raw/source.csv', b'replacement')
    store = VersionedArtifacts(client, 'offline')
    assert store.read(original) == b'original'
    assert client.calls[-1] == ('get', original.key, original.version)


@pytest.mark.parametrize('change', ['missing', 'corrupt', 'truncated', 'wrong_version', 'oversized', 'wrong_length'])
def test_versioned_reader_fails_closed(change):
    client = MemoryVersionedClient()
    identity = client.seed('raw/source.csv', b'original')
    if change == 'missing':
        del client.objects[identity.key, identity.version]
    elif change == 'corrupt':
        client.objects[identity.key, identity.version] = b'xxxxxxxx'
    elif change == 'truncated':
        client.objects[identity.key, identity.version] = b'orig'
    elif change == 'wrong_version':
        identity = replace(identity, version='missing-version')
    elif change == 'oversized':
        identity = replace(identity, size=1000)
    else:
        get = client.get_object
        def wrong(**kwargs):
            result = get(**kwargs)
            result['VersionId'] = 'substituted'
            return result
        client.get_object = wrong
    with pytest.raises((StorageIntegrityError, KeyError)):
        VersionedArtifacts(client, 'offline', max_bytes=100).read(identity)


def test_write_once_roundtrip_and_read_budget():
    client = MemoryVersionedClient()
    store = VersionedArtifacts(client, 'offline', max_bytes=10, max_total_bytes=10)
    key = f'artifact-candidates/{uuid4()}/data.parquet'
    identity = store.put(key, b'data')
    assert store.read(identity) == b'data'
    with pytest.raises(StorageIntegrityError):
        store.read(identity)
    with pytest.raises(StorageIntegrityError):
        store.put(key, b'new')


@pytest.mark.parametrize('key', ['raw/source.csv', 'artifact-candidates/../source',
    'artifact-candidates/not-a-uuid/data', f'artifact-candidates/{uuid4()}/../source'])
def test_unauthorized_writes_are_rejected_before_sdk_call(key):
    client = MemoryVersionedClient()
    with pytest.raises(StorageIntegrityError):
        VersionedArtifacts(client, 'offline').put(key, b'data')
    assert not client.calls


def test_stream_is_closed_on_oversized_or_invalid_response():
    body = io.BytesIO(b'excess payload')
    class Client:
        def get_object(self, **kwargs):
            return {'Body': body, 'ContentLength': 4, 'VersionId': 'pinned'}
    identity = ObjectIdentity('raw/source', 'pinned', hashlib.sha256(b'data').hexdigest(), 4)
    with pytest.raises(StorageIntegrityError):
        VersionedArtifacts(Client(), 'offline').read(identity)
    assert body.closed


def test_unversioned_objects_are_not_accepted():
    class Client:
        def put_object(self, **kwargs):
            return {}
    with pytest.raises(StorageIntegrityError):
        VersionedArtifacts(Client(), 'offline').put(f'artifact-candidates/{uuid4()}/data', b'data')


@pytest.mark.parametrize('payload',[b'',b'id,id\n1,2\n',b'id\n1,2\n',b'_source_file_id\n7\n',b'id\n'+b'1\n'*100001],
                         ids=['empty','duplicate-columns','ragged','reserved-lineage','too-many-rows'])
def test_raw_csv_dimensions_fail_before_pandas_allocation(payload):
    from app.storage.versioned_artifacts import validate_csv_source
    with pytest.raises(StorageIntegrityError): validate_csv_source(payload)
