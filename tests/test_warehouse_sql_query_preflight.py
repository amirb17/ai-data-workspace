"""Step 411A: strict contract linkage cannot mint authorization."""
from dataclasses import replace
from pathlib import Path
import ast

import pytest

from app.query_governance.providers.base import CompiledQuery,ExecutionPermit
from app.warehouse.sql_query_preflight import inspect_warehouse_query_scope
from tests.test_warehouse_sql_provider_metadata_verifier import binding


def query(bound=None):
    bound=bound if bound is not None else binding()
    return CompiledQuery(
        workspace_id=7,request_token="1"*64,intent_sha256="2"*64,
        sql="SELECT c0 FROM `synthetic-project.dr_dev_w7.m12_v44_g1_p77_r2` LIMIT 10",
        physical_tables=tuple(m.physical_table for m in bound.models),
        compiler_version=2,
    )


def permit():
    return ExecutionPermit(
        reservation_id=1,workspace_id=7,provider="BIGQUERY",
        request_token="1"*64,intent_sha256="2"*64,
        reserved_bytes=4096,maximum_bytes_billed=4096,output_rows=10,
    )


def inspect(*,bound=None,compiled=None,approved=None,workspace_id=7):
    return inspect_warehouse_query_scope(
        workspace_id=workspace_id,
        binding=binding() if bound is None else bound,
        query=query() if compiled is None else compiled,
        permit=permit() if approved is None else approved,
    )


def test_consistent_governance_contract_remains_non_authorizing():
    result=inspect()
    assert result.workspace_id==7
    assert result.table_count==1
    assert result.maximum_bytes_billed==4096
    assert result.output_rows==10
    assert result.ready_for_execution is False
    assert result.query_execution_authorized is False


@pytest.mark.parametrize("value",[
    lambda:{"workspace_id":8},
    lambda:{"compiled":replace(query(),workspace_id=8)},
    lambda:{"approved":replace(permit(),workspace_id=8)},
    lambda:{"approved":replace(permit(),provider="OTHER")},
    lambda:{"approved":replace(permit(),request_token="f"*64)},
    lambda:{"approved":replace(permit(),intent_sha256="f"*64)},
    lambda:{"approved":replace(permit(),reserved_bytes=0)},
    lambda:{"compiled":replace(query(),compiler_version=1)},
    lambda:{"compiled":replace(query(),physical_tables=(
        "synthetic-project.dr_dev_w7.m12_v44_g1_p77_r3",))},
])
def test_scope_budget_and_identity_drift_fails_closed(value):
    with pytest.raises(ValueError):
        inspect(**value())


def test_nonexecutable_publication_binding_required():
    with pytest.raises(ValueError):
        inspect(bound=replace(binding(),executable=True))


def test_duplicate_models_disallowed():
    duplicate=replace(binding(),models=(binding().models[0],binding().models[0]))
    with pytest.raises(ValueError):
        inspect(bound=duplicate)


def test_exact_ordered_join_table_scope():
    from app.warehouse.sql_physical_binding import InternalModelBinding
    second=InternalModelBinding(13,45,67,1,79,2,89,"c"*64,"d"*64,
        "synthetic-project.dr_dev_w7.m13_v45_g1_p79_r2")
    joined=replace(binding(),models=(*binding().models,second))
    good=query(joined)
    assert inspect(bound=joined,compiled=good).table_count==2
    with pytest.raises(ValueError):
        inspect(bound=joined,compiled=replace(good,physical_tables=tuple(reversed(good.physical_tables))))


def test_preflight_is_pure_without_db_cloud_or_own_execution_permit():
    path=Path(__file__).parents[1]/"app/warehouse/sql_query_preflight.py"
    root=ast.parse(path.read_text(encoding="utf-8"))
    modules=[n.module for n in ast.walk(root) if isinstance(n,ast.ImportFrom)]
    assert not any(m and m.startswith((
        "app.db","app.services","google","app.query_governance.providers.bigquery",
    )) for m in modules)
    assert all(n.name not in ("execute","reserve","issue_permit") for n in ast.walk(root)
               if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)))
