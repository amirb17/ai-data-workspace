"""414D4b: compiler fingerprint is separate from retry-safe attempt token."""
from dataclasses import replace
from pathlib import Path

import pytest

from app.services.warehouse_query_attempt_service import WarehouseQueryAttempt
from app.services.warehouse_sql_stored_draft_service import StoredWarehouseLogicalDraft
from app.services.warehouse_sql_attempt_identity_bridge_service import (
    bind_compiler_identity_to_attempt_without_dispatch as bridge,
)
from app.warehouse.sql_single_model_compiler import compile_single_model_sql
from tests.test_warehouse_sql_single_model_compiler import input_pair
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_sql_stored_draft_414d4a import (
    draft_registry_pg,attempt as create_attempt,register,resolve,
)


def fake_attempt(identifier=10,token="e"*64):
    return WarehouseQueryAttempt(
        attempt_id=identifier,attempt_handle="a"*32,
        request_token=token,logical_intent_sha256="b"*64,reused=False,
    )


def test_same_compiled_intent_different_attempts_have_distinct_tokens():
    draft,binding=input_pair()
    original=compile_single_model_sql(workspace_id=7,draft=draft,binding=binding)
    a=bridge(workspace_id=7,attempt=fake_attempt(10,"e"*64),
             stored_draft=StoredWarehouseLogicalDraft(10,draft,True),
             binding=binding)
    b=bridge(workspace_id=7,attempt=fake_attempt(11,"f"*64),
             stored_draft=StoredWarehouseLogicalDraft(11,draft,True),
             binding=binding)
    assert a.query.intent_sha256==b.query.intent_sha256==original.intent_sha256
    assert a.query.sql==b.query.sql==original.sql
    assert a.query.request_token=="e"*64
    assert b.query.request_token=="f"*64
    assert a.deterministic_compiler_token==b.deterministic_compiler_token
    assert a.deterministic_compiler_token==original.request_token
    assert a.query.request_token!=a.deterministic_compiler_token
    assert not a.execution_authorized
    assert not a.provider_dispatched
    assert not a.reservation_created


def test_retry_same_attempt_produces_identical_query_and_token():
    draft,binding=input_pair()
    a=fake_attempt()
    proof=StoredWarehouseLogicalDraft(a.attempt_id,draft,True)
    first=bridge(workspace_id=7,attempt=a,stored_draft=proof,binding=binding)
    retry=bridge(workspace_id=7,attempt=replace(a,reused=True),
                 stored_draft=proof,binding=binding)
    assert first==retry
    assert first.query.compiler_version==2


def test_changed_physical_receipt_changes_intent_but_not_attempt_token():
    draft,binding=input_pair()
    proof=StoredWarehouseLogicalDraft(10,draft,True)
    a=fake_attempt()
    base=bridge(workspace_id=7,attempt=a,stored_draft=proof,binding=binding)
    m=binding.models[0]
    changed=replace(binding,models=(replace(m,serving_materialization_id=999),))
    other=bridge(workspace_id=7,attempt=a,stored_draft=proof,binding=changed)
    assert base.query.request_token==other.query.request_token
    assert base.query.intent_sha256!=other.query.intent_sha256
    # Caller must reject a changed physical fingerprint on an already
    # reserved attempt; this pure helper does not authorize reuse.


def test_mismatched_attempt_identity_or_unpersisted_draft_rejected():
    draft,binding=input_pair()
    a=fake_attempt()
    with pytest.raises(ValueError,match="Immutable owned attempt"):
        bridge(workspace_id=7,attempt=a,
               stored_draft=StoredWarehouseLogicalDraft(11,draft,True),
               binding=binding)
    with pytest.raises(ValueError,match="Immutable owned attempt"):
        bridge(workspace_id=7,attempt=a,
               stored_draft=StoredWarehouseLogicalDraft(10,draft,False),
               binding=binding)


def test_mutated_draft_or_binding_fails_compiler_revalidation():
    draft,binding=input_pair()
    stored=StoredWarehouseLogicalDraft(10,replace(draft,row_limit=999),True)
    with pytest.raises(ValueError):
        bridge(workspace_id=7,attempt=fake_attempt(),
               stored_draft=stored,binding=binding)


def test_actual_db_attempt_token_survives_reloaded_logical_draft(draft_registry_pg):
    f,model,version=draft_registry_pg
    attempt,_=create_attempt(f,model,version)
    register(f,model,attempt)
    stored=resolve(f,attempt)
    from app.warehouse.sql_physical_binding import (
        InternalDraftBinding,InternalModelBinding,InternalColumnBinding,
    )
    binding=InternalDraftBinding(
        (InternalModelBinding(
            model,version,123,1,212,1,312,
            "a"*64,"b"*64,
            f"synthetic-project.dr_dev_w{f['workspace']}.m{model}_v{version}_g1_p212_r1",
        ),),
        (InternalColumnBinding("","id","c0","STRING"),),
    )
    result=bridge(workspace_id=f["workspace"],attempt=attempt,
                  stored_draft=stored,binding=binding)
    from app.services.warehouse_query_attempt_service import (
        inspect_owned_warehouse_attempt,
    )
    from tests.test_warehouse_sql_admission_postgres import service
    reread=inspect_owned_warehouse_attempt(
        governance=service(f),attempt_handle=attempt.attempt_handle,
    )
    assert result.query.request_token==reread.request_token
    assert result.query.request_token==attempt.request_token
    assert not result.execution_authorized


def test_bridge_has_no_reservation_or_provider_calls():
    src=(Path(__file__).resolve().parents[1]/
         "app/services/warehouse_sql_attempt_identity_bridge_service.py"
        ).read_text(encoding="utf-8")
    for fragment in ("provider.execute(","governance.claim(","governance.admit("):
        assert fragment not in src
    assert "execution_authorized: bool = False" in src
