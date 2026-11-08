"""Offline admission boundaries; no storage SDK, database or publication."""
import hashlib
import io
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services.dataset_gold_serving_service import EvidenceStore,ServingRejected,reconstruct


def test_storage_must_be_explicitly_injected():
    with pytest.raises(ValueError,match='Injected'):EvidenceStore(None)


def test_invalid_parquet_is_rejected_with_safe_reason():
    with pytest.raises(ServingRejected,match='^Malformed Parquet evidence$'):
        EvidenceStore(object())._check(b'PAR1synthetic-broken-footer')


@pytest.mark.parametrize('rows,columns',[(1001,1),(1,129)])
def test_encoded_parquet_cannot_bypass_state_shape_bounds(rows,columns):
    frame=pd.DataFrame({f'c{i}':range(rows) for i in range(columns)})
    output=io.BytesIO();frame.to_parquet(output,index=False)
    with pytest.raises(ServingRejected,match='decoded-state'):
        EvidenceStore(object())._check(output.getvalue())


def test_object_byte_bound_precedes_parquet_decode():
    with pytest.raises(ServingRejected,match='object bound'):
        EvidenceStore(object())._check(b'x'*(32*1024*1024+1))


def test_cumulative_io_budget_is_not_reset_per_object():
    store=EvidenceStore(object());store.consumed=128*1024*1024
    with pytest.raises(ServingRejected,match='I/O bound'):store._check(b'x')


def test_new_dataset_composition_rejects_exact_v2_before_legacy_build():
    body=b'{"manifest_version":2}'
    state=dict(manifest_key='synthetic/manifest.json',manifest_sha256=hashlib.sha256(body).hexdigest())
    store=SimpleNamespace(read=lambda *args,**kwargs:body)
    with pytest.raises(RuntimeError,match='Exact Gold V2'):
        reconstruct(store,1,[dict(state=state,application={},policy={})])
