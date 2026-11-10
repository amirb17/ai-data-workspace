"""Step 411B: claimed reservation scope must fail closed on replay/drift."""
from copy import deepcopy
from pathlib import Path
import ast

import pytest

from app.warehouse.sql_claimed_reservation_guard import (
    inspect_claimed_warehouse_reservation,
)
from tests.test_warehouse_sql_provider_metadata_verifier import binding
from tests.test_warehouse_sql_query_preflight import query


def reservation():
    return {
        "reservation_id":23,
        "workspace_id":7,
        "owner_key":"usr:test",
        "consumer":"SQL",
        "provider":"BIGQUERY",
        "status":"EXECUTING",
        "request_token":"1"*64,
        "intent_sha256":"2"*64,
        "reserved_bytes":4096,
        "maximum_bytes_billed":4096,
        "output_rows":10,
    }


def check(**changes):
    values=dict(workspace_id=7,owner_key="usr:test",consumer="SQL",
                reservation=reservation(),binding=binding(),query=query())
    values.update(changes)
    return inspect_claimed_warehouse_reservation(**values)


def test_valid_claim_remains_a_non_authorizing_consistency_check():
    result=check()
    assert result.reservation_id==23
    assert result.workspace_id==7
    assert result.publication_model_count==1
    assert result.claimed_reservation_consistent
    assert result.execution_authorized is False


@pytest.mark.parametrize("field,value",[
    ("status","ACTIVE"),("status","COMPLETED"),("status","FAILED"),
    ("status","RELEASED"),("owner_key","usr:another"),
    ("consumer","ASK"),("provider","DUCKDB"),
    ("workspace_id",8),("request_token","f"*64),
    ("intent_sha256","f"*64),("reserved_bytes",0),
    ("maximum_bytes_billed",0),("output_rows",1001),
])
def test_replay_and_claim_scope_drift_rejected(field,value):
    bad={**reservation(),field:value}
    with pytest.raises(ValueError):
        check(reservation=bad)


def test_current_owner_must_match_claimed_owner():
    with pytest.raises(ValueError):
        check(owner_key="usr:another")


def test_approved_query_tables_must_match_publication_bindings():
    from dataclasses import replace
    other=replace(query(),physical_tables=(
        "synthetic-project.dr_dev_w7.m12_v44_g1_p77_r3",))
    with pytest.raises(ValueError):
        check(query=other)


def test_guard_never_mints_claims_calls_db_or_queries_provider():
    path=Path(__file__).parents[1]/"app/warehouse/sql_claimed_reservation_guard.py"
    module=ast.parse(path.read_text(encoding="utf-8"))
    imports=[n.module for n in ast.walk(module) if isinstance(n,ast.ImportFrom)]
    assert not any(m and m.startswith((
        "app.db","app.services","google","app.query_governance.providers.bigquery",
    )) for m in imports)
    assert all(n.name not in ("execute","claim","reserve","release")
               for n in ast.walk(module) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)))
