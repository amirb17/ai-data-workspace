"""Step 412A: immutable, single-model AST to BigQuery SQL (no execution)."""
from dataclasses import replace
import ast
from pathlib import Path

import pytest

from app.warehouse.sql_single_model_compiler import compile_single_model_sql
from app.warehouse.sql_draft_planner import WarehouseSqlDraft
from app.warehouse.sql_physical_binding import bind_draft
from tests.test_warehouse_sql_physical_binding import source


def input_pair():
    draft=WarehouseSqlDraft(
        normalized_sql="SELECT amount FROM model_12 LIMIT 5",
        model_id=12,model_version_id=44,
        projected_columns=("amount",),row_limit=5,
    )
    bound=bind_draft(draft,fact=source(12,44,"FACT"))
    return draft,bound


def compile_with(draft=None,bound=None):
    initial,physical=input_pair()
    return compile_single_model_sql(
        workspace_id=7,
        draft=initial if draft is None else draft,
        binding=physical if bound is None else bound,
    )


def test_single_model_compiles_only_backend_pinned_fields_and_table():
    result=compile_with()
    assert result.sql==(
        "SELECT `c1` AS `amount` FROM "
        "`synthetic-project.dr_dev_w7.m12_v44_g1_p212_r2` LIMIT 5"
    )
    assert result.compiler_version==2
    assert result.physical_tables==(
        "synthetic-project.dr_dev_w7.m12_v44_g1_p212_r2",
    )
    assert len(result.intent_sha256)==64
    assert len(result.request_token)==64
    assert result.request_token!=result.intent_sha256
    assert compile_with()==result



def test_approved_dimension_compiles_using_real_model_type():
    draft=WarehouseSqlDraft(
        normalized_sql="SELECT product_id FROM model_12 LIMIT 10",
        model_id=12,model_version_id=44,
        projected_columns=("product_id",),row_limit=10,
    )
    bound=bind_draft(draft,fact=source(12,44,"DIMENSION"))
    assert bound.models[0].model_type=="DIMENSION"
    compiled=compile_single_model_sql(
        workspace_id=7,draft=draft,binding=bound,
    )
    assert compiled.sql==(
        "SELECT `c0` AS `product_id` FROM "
        "`synthetic-project.dr_dev_w7.m12_v44_g1_p212_r2` LIMIT 10"
    )


def test_dimension_and_fact_change_immutable_compilation_identity():
    draft=WarehouseSqlDraft(
        normalized_sql="SELECT product_id FROM model_12 LIMIT 10",
        model_id=12,model_version_id=44,
        projected_columns=("product_id",),row_limit=10,
    )
    fact=compile_single_model_sql(workspace_id=7,draft=draft,
                                  binding=bind_draft(draft,fact=source(12,44,"FACT")))
    dim=compile_single_model_sql(workspace_id=7,draft=draft,
                                 binding=bind_draft(draft,fact=source(12,44,"DIMENSION")))
    assert dim.sql==fact.sql
    assert dim.intent_sha256!=fact.intent_sha256

def test_change_of_serving_receipt_changes_intent_and_request_token():
    draft,bound=input_pair()
    altered=replace(bound,models=(
        replace(bound.models[0],serving_materialization_id=999),))
    original=compile_with(draft,bound)
    newer=compile_with(draft,altered)
    assert original.sql==newer.sql
    assert original.intent_sha256!=newer.intent_sha256
    assert original.request_token!=newer.request_token


@pytest.mark.parametrize("sql",[
    "SELECT amount FROM model_12",
    "SELECT * FROM model_12 LIMIT 5",
    "SELECT amount FROM external.dataset LIMIT 5",
    "SELECT amount FROM model_12 WHERE amount > 0 LIMIT 5",
    "SELECT SUM(amount) FROM model_12 LIMIT 5",
    "SELECT amount FROM model_12 UNION ALL SELECT amount FROM model_12 LIMIT 5",
    "DELETE FROM model_12",
    "SELECT amount FROM model_12 LIMIT 5000",
])
def test_source_ast_must_satisfy_original_strict_planner(sql):
    draft,bound=input_pair()
    with pytest.raises(Exception):
        compile_with(replace(draft,normalized_sql=sql),bound)


def test_manual_draft_projection_change_is_rejected():
    draft,bound=input_pair()
    with pytest.raises(ValueError):
        compile_with(replace(draft,projected_columns=("id",)),bound)


def test_manual_row_limit_change_is_rejected():
    draft,bound=input_pair()
    with pytest.raises(ValueError):
        compile_with(replace(draft,row_limit=8),bound)


def test_wrong_model_or_version_rejected():
    draft,bound=input_pair()
    with pytest.raises(ValueError):
        compile_with(replace(draft,model_id=1),bound)
    with pytest.raises(ValueError):
        compile_with(replace(draft,model_version_id=1),bound)


@pytest.mark.parametrize("name",["c0` UNION SELECT secret","amount;DROP TABLE x","other"])
def test_untrusted_physical_column_or_alias_rejected(name):
    draft,bound=input_pair()
    changed=replace(bound,columns=(replace(bound.columns[0],physical_name=name),))
    with pytest.raises(ValueError):
        compile_with(draft,changed)


def test_user_selected_physical_table_is_rejected_by_query_type():
    draft,bound=input_pair()
    changed=replace(bound,models=(replace(
        bound.models[0],physical_table="other-project.other_schema.secret"),))
    with pytest.raises(ValueError):
        compile_with(draft,changed)


def test_no_provider_db_or_execution_imports():
    path=Path(__file__).parents[1]/"app/warehouse/sql_single_model_compiler.py"
    node=ast.parse(path.read_text(encoding="utf-8"))
    imports=[n.module for n in ast.walk(node) if isinstance(n,ast.ImportFrom)]
    assert not any(n and n.startswith(("app.db","app.services","google","psycopg"))
                   for n in imports)
    assert not any(n.name in ("execute","claim","reserve","query")
                   for n in ast.walk(node) if isinstance(n,ast.FunctionDef))
