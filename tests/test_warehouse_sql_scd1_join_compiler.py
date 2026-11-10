"""Step 412B: SCD1 join compilation requires complete reviewed source evidence."""
from dataclasses import replace
from pathlib import Path
import ast

import pytest

from app.warehouse.relationship_contract import WarehouseRelationship
from app.warehouse.sql_join_draft_planner import plan_join_draft
from app.warehouse.sql_physical_binding import bind_draft
from app.warehouse.sql_scd1_join_compiler import compile_current_member_join
from tests.test_warehouse_sql_physical_binding import source


def pair():
    fact=source(12,44,"FACT")
    dim=source(20,55,"DIMENSION")
    relation=WarehouseRelationship(
        workspace_id=7,fact_model_id=12,fact_model_version_id=44,
        dimension_model_id=20,dimension_model_version_id=55,
        name="Product current member",
        key_pairs=[{"fact_column":"product_id","dimension_column":"product_id"}],
        temporal_policy="CURRENT_MEMBER",
    )
    catalog={"workspace_id":7,"models":[
        {"model_id":s.model_id,"model_version_id":s.model_version_id,
         "model_type":s.model_type,"status":"APPROVED",
         "columns":[{"name":name,"role":"ATTRIBUTE"} for name,_,_ in s.columns]}
        for s in (fact,dim)
    ]}
    edges=[{"relationship_id":9,"workspace_id":7,
            "fact_model_id":12,"fact_model_version_id":44,
            "dimension_model_id":20,"dimension_model_version_id":55,
            "temporal_policy":"CURRENT_MEMBER","cardinality":"MANY_TO_ONE",
            "key_pairs":[{"fact_column":"product_id","dimension_column":"product_id"}]}]
    draft=plan_join_draft(
        "SELECT f.amount, d.category FROM model_12 AS f "
        "INNER JOIN model_20 AS d ON f.product_id = d.product_id LIMIT 5",
        workspace_id=7,catalog=catalog,relationships=edges,relationship_id=9,
    )
    return draft,bind_draft(draft,fact=fact,dimension=dim),relation,fact,dim


def compile_with(**kwargs):
    draft,binding,relation,fact,dim=pair()
    arguments=dict(
        workspace_id=7,draft=draft,binding=binding,relationship=relation,
        fact=fact,dimension=dim,
    )
    arguments.update(kwargs)
    return compile_current_member_join(**arguments)


def test_reviewed_scd1_join_generates_only_pinned_bigquery_identifiers():
    result=compile_with()
    assert result.sql == (
        "SELECT f.`c1` AS `f_amount`, d.`c2` AS `d_category` "
        "FROM `synthetic-project.dr_dev_w7.m12_v44_g1_p212_r2` AS f "
        "INNER JOIN `synthetic-project.dr_dev_w7.m20_v55_g1_p220_r2` AS d "
        "ON f.`c0` = d.`c0` LIMIT 5"
    )
    assert len(result.physical_tables)==2
    assert result.compiler_version==2
    assert compile_with()==result


def test_receipt_change_changes_intent_without_changing_physical_sql():
    d,b,r,f,dim=pair()
    edited=replace(dim,serving_materialization_id=988)
    new_model=replace(b.models[1],serving_materialization_id=988)
    result=compile_with(draft=d,binding=replace(b,models=(b.models[0],new_model)),
                        relationship=r,fact=f,dimension=edited)
    original=compile_with()
    assert result.sql==original.sql
    assert result.intent_sha256!=original.intent_sha256
    assert result.request_token!=original.request_token


@pytest.mark.parametrize("change",[
    {"workspace_id":8},
    {"draft":None},
    {"binding":None},
])
def test_missing_or_wrong_scope_fails_closed(change):
    with pytest.raises((ValueError,AttributeError,TypeError)):
        compile_with(**change)


def test_unreviewed_key_change_is_rejected():
    d,b,r,f,dim=pair()
    different=replace(r,key_pairs=(
        type(r.key_pairs[0])(fact_column="amount",dimension_column="product_id"),
    ))
    with pytest.raises(Exception):
        compile_with(relationship=different)


def test_draft_join_predicate_injection_rejected():
    d,b,r,f,dim=pair()
    tampered=replace(
        d,normalized_sql=d.normalized_sql.replace(
            "LIMIT 5","AND f.amount = d.category LIMIT 5"))
    with pytest.raises(Exception):
        compile_with(draft=tampered)


def test_partial_or_reordered_publication_binding_rejected():
    d,b,r,f,dim=pair()
    with pytest.raises(ValueError):
        compile_with(binding=replace(b,models=tuple(reversed(b.models))))
    with pytest.raises(ValueError):
        compile_with(binding=replace(b,models=b.models[:1]))


def test_unprojected_join_key_is_resolved_from_trusted_sources():
    d,b,r,f,dim=pair()
    assert all(c.logical_name!="product_id" for c in b.columns)
    sql=compile_with().sql
    assert "ON f.`c0` = d.`c0`" in sql


def test_source_schema_key_type_mismatch_is_rejected():
    d,b,r,f,dim=pair()
    changed=replace(dim,columns=(("product_id","c0","INT64"),*dim.columns[1:]))
    with pytest.raises(ValueError):
        compile_with(dimension=changed)


def test_scd2_policy_cannot_be_compiled_as_current_member():
    d,b,r,f,dim=pair()
    alternate=WarehouseRelationship(
        workspace_id=7,fact_model_id=12,fact_model_version_id=44,
        dimension_model_id=20,dimension_model_version_id=55,
        name="Historical",temporal_policy="EVENT_TIME_AS_OF",
        fact_event_time_column="purchased_at",
        key_pairs=[{"fact_column":"product_id","dimension_column":"product_id"}],
    )
    with pytest.raises(ValueError):
        compile_with(relationship=alternate)


def test_module_has_no_db_cloud_execution_or_dispatch():
    module=ast.parse(
        (Path(__file__).parents[1]/"app/warehouse/sql_scd1_join_compiler.py")
        .read_text(encoding="utf-8")
    )
    imports=[n.module for n in ast.walk(module) if isinstance(n,ast.ImportFrom)]
    assert not any(n and n.startswith(("app.db","app.services","google","psycopg"))
                   for n in imports)
    assert not any(n.name in ("execute","claim","reserve")
                   for n in ast.walk(module) if isinstance(n,ast.FunctionDef))
