"""Step 412D: compiler, publication, provider metadata must agree twice."""
from dataclasses import replace
from pathlib import Path
import ast

import pytest

from app.services.warehouse_sql_compiled_evidence_service import (
    inspect_compiled_warehouse_evidence,
)
from app.warehouse.sql_single_model_compiler import compile_single_model_sql
from app.warehouse.sql_scd1_join_compiler import compile_current_member_join
from app.warehouse.sql_scd2_join_compiler import compile_historical_join
from tests.test_warehouse_sql_provider_metadata_verifier import Fake, SCHEMA, binding
from tests.test_warehouse_sql_single_model_compiler import input_pair
from tests.test_warehouse_sql_scd1_join_compiler import pair as scd1_pair
from tests.test_warehouse_sql_scd2_join_compiler import inputs as scd2_inputs


def single():
    draft,physical=input_pair()
    # The unit fake provider metadata belongs to the separate one-column
    # demonstration binding, so keep this test's table/checksum consistent.
    from dataclasses import replace
    demo=binding()
    from app.warehouse.sql_draft_planner import WarehouseSqlDraft
    draft=WarehouseSqlDraft(
        "SELECT id FROM model_12 LIMIT 5",12,44,("id",),5,
    )
    compiler=lambda current:compile_single_model_sql(
        workspace_id=7,draft=draft,binding=current,
    )
    return demo,compiler


def inspected(bound,compiler,*,resolver=None,client=None,query=None):
    return inspect_compiled_warehouse_evidence(
        workspace_id=7,
        query=compiler(bound) if query is None else query,
        resolve_current=(lambda:bound) if resolver is None else resolver,
        compile_current=compiler,
        expected_schemas={12:SCHEMA},
        client=Fake() if client is None else client,
        project="synthetic-project",
    )


def test_compiled_single_model_evidence_is_diagnostic_only():
    bound,compiler=single()
    inspected_bound=inspected(bound,compiler)
    assert inspected_bound.model_count==1
    assert inspected_bound.table_metadata_verified
    assert inspected_bound.current_publication_checked_after_metadata
    assert inspected_bound.query_execution_authorized is False
    assert inspected_bound.row_contents_verified is False


def test_sql_text_tampering_blocked_before_provider_read():
    bound,compiler=single()
    original=compiler(bound)
    fake=Fake()
    altered=replace(original,sql=original.sql+" -- unreviewed")
    with pytest.raises(ValueError,match="trusted current compilation"):
        inspected(bound,compiler,query=altered,client=fake)
    assert fake.calls==[]


def test_query_intent_or_token_tampering_blocked():
    bound,compiler=single()
    original=compiler(bound)
    for changed in (
        replace(original,request_token="f"*64),
        replace(original,intent_sha256="f"*64),
    ):
        with pytest.raises(ValueError):
            inspected(bound,compiler,query=changed)


def test_provider_read_can_advance_publication_and_gate_rejects():
    bound,compiler=single()
    changed=replace(bound,models=(replace(
        bound.models[0],serving_materialization_id=777),))
    reads=[]
    def resolver():
        reads.append(1)
        return bound if len(reads)==1 else changed
    with pytest.raises(ValueError,match="WAREHOUSE_PUBLICATION_CHANGED"):
        inspected(bound,compiler,resolver=resolver)
    assert len(reads)==2


def test_provider_metadata_drift_rejects_without_postread_success():
    bound,compiler=single()
    fake=Fake()
    fake.table.labels={**fake.table.labels,"revision":"invalid"}
    with pytest.raises(Exception):
        inspected(bound,compiler,client=fake)


def test_compiler_mismatch_after_metadata_fails_closed():
    bound,compiler=single()
    calls=[]
    def rebuild(b):
        calls.append(1)
        result=compiler(b)
        return result if len(calls)==1 else replace(result,sql=result.sql+" ")
    with pytest.raises(ValueError,match="WAREHOUSE_COMPILED_QUERY_CHANGED"):
        inspected(bound,rebuild,query=compiler(bound))
    assert len(calls)==2


def test_single_and_two_model_compilers_remain_separate_and_nonexecuting():
    from tests.test_warehouse_sql_physical_binding import source
    d,b,r,f,dim=scd1_pair()
    joined=compile_current_member_join(
        workspace_id=7,draft=d,binding=b,relationship=r,fact=f,dimension=dim,
    )
    assert len(joined.physical_tables)==2
    assert joined.compiler_version==2
    d2,b2,s2,f2,dim2=scd2_inputs()
    historical=compile_historical_join(
        workspace_id=7,draft=d2,binding=b2,specification=s2,fact=f2,dimension=dim2,
    )
    assert len(historical.physical_tables)==2
    assert historical.intent_sha256!=joined.intent_sha256


def test_evidence_service_does_not_mint_permit_or_execute_sql():
    p=Path(__file__).parents[1]/"app/services/warehouse_sql_compiled_evidence_service.py"
    tree=ast.parse(p.read_text(encoding="utf-8"))
    imports=[x.module for x in ast.walk(tree) if isinstance(x,ast.ImportFrom)]
    assert not any(x and x.startswith(("app.db","google","psycopg"))
                   for x in imports)
    assert not any(x.name in ("execute","reserve","claim","issue_permit")
                   for x in ast.walk(tree) if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)))
