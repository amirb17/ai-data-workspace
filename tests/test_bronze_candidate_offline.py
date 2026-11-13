"""No AWS/DB needed: immutable Bronze candidate and failure isolation."""
import io
from uuid import uuid4

import pandas as pd
import pytest

from app.processing_worker.bronze_candidate import build_bronze_candidate
from app.storage.versioned_artifacts import MemoryVersionedClient, ObjectIdentity, StorageIntegrityError


def test_bronze_candidate_preserves_lexical_csv_and_pins_parquets():
    client = MemoryVersionedClient()
    source = client.seed('raw/test/source.csv', b'customer_id,amount\n001,1.50\n002,2.00\n')
    checks = []
    result = build_bronze_candidate(client, 'private-test-bucket', source, uuid4(),
                                    checkpoint=lambda: checks.append(1))
    assert result.rows == 2
    assert result.columns == ('customer_id','amount')
    assert len(result.schema_hash) == 64
    assert result.source == source
    assert result.inferred.key != result.lexical.key
    assert result.inferred.version != result.lexical.version
    assert result.inferred.key.startswith('artifact-candidates/')
    original = client.objects[result.lexical.key, result.lexical.version]
    lexical = pd.read_parquet(io.BytesIO(original))
    assert lexical['customer_id'].tolist() == ['001','002']
    assert lexical['amount'].tolist() == ['1.50','2.00']
    inferred = pd.read_parquet(io.BytesIO(client.objects[result.inferred.key, result.inferred.version]))
    assert inferred['_source_file_sha256'].tolist() == [source.sha256] * 2
    assert len(checks) > 3
    # Staging candidates do not move a publication head or mutate DB metadata.
    assert set(k[0] for k in client.objects) == {
        source.key,result.inferred.key,result.lexical.key
    }


def test_stale_or_tampered_object_fails_before_any_writes():
    client = MemoryVersionedClient()
    source = client.seed('raw/test/source.csv', b'id,name\n1,A\n')
    bad = ObjectIdentity(source.key, source.version, '0'*64, source.size)
    with pytest.raises(StorageIntegrityError):
        build_bronze_candidate(client, 'private-test-bucket', bad, uuid4())
    assert not client.calls or all(call[0] != 'put' for call in client.calls)


def test_invalid_csv_and_oversized_source_fail_closed():
    client = MemoryVersionedClient()
    bad = client.seed('raw/test/duplicate.csv', b'id,id\n1,2\n')
    with pytest.raises(StorageIntegrityError):
        build_bronze_candidate(client, 'private-test-bucket', bad, uuid4())
    fake_large = ObjectIdentity('raw/test/large.csv', 'v1','0'*64,5*1024*1024+1)
    with pytest.raises(StorageIntegrityError):
        build_bronze_candidate(client,'private-test-bucket',fake_large,uuid4())
    assert not any(call[0] == 'put' for call in client.calls)


def test_worker_checkpoint_failure_does_not_publish_or_complete():
    client = MemoryVersionedClient()
    source = client.seed('raw/test/source.csv',b'id\n1\n')
    def denied():
        raise RuntimeError('lease revoked')
    with pytest.raises(RuntimeError, match='lease revoked'):
        build_bronze_candidate(client,'private-test-bucket',source,uuid4(),checkpoint=denied)
    assert not any(call[0] == 'put' for call in client.calls)
