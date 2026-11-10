"""Step 412C: SCD2 UTC half-open temporal join compilation, never executed."""
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import ast

import pytest

from app.warehouse.sql_physical_binding import bind_draft
from app.warehouse.sql_scd2_join_compiler import compile_historical_join
from tests.test_warehouse_sql_physical_binding import source
from tests.test_warehouse_sql_scd2_draft_planner import ctx, draft


def inputs():
    specification,_=ctx()
    reviewed=draft()
    fact=source(12,44,"FACT")
    dim=source(20,55,"DIMENSION")
    # Complete two-column reviewed business keys (product_id and store_id).
    fact=replace(fact,columns=(
        ("product_id","c0","STRING"),("amount","c1","BIGNUMERIC"),
        ("category","c2","STRING"),("purchased_at","c3","TIMESTAMP"),
        ("store_id","c4","STRING"),
    ))
    dim=replace(dim,columns=(
        ("product_id","c0","STRING"),("amount","c1","BIGNUMERIC"),
        ("category","c2","STRING"),("changed_at","c3","TIMESTAMP"),
        ("store_id","c4","STRING"),
    ),business_keys=("product_id","store_id"))
    bound=bind_draft(reviewed,fact=fact,dimension=dim)
    return reviewed,bound,specification,fact,dim


def compile_with(**changes):
    reviewed,bound,spec,fact,dim=inputs()
    args=dict(workspace_id=7,draft=reviewed,binding=bound,
              specification=spec,fact=fact,dimension=dim)
    args.update(changes)
    return compile_historical_join(**args)


def test_exact_composite_historical_join_is_pinned_and_nonexecuting():
    result=compile_with()
    assert result.sql==(
        "SELECT f.`c1` AS `f_amount`, d.`c2` AS `d_category` "
        "FROM `synthetic-project.dr_dev_w7.m12_v44_g1_p212_r2` AS f "
        "INNER JOIN `synthetic-project.dr_dev_w7.m20_v55_g1_p220_r2` AS d "
        "ON f.`c0` = d.`c0` AND f.`c4` = d.`c4` "
        "AND f.`c3` >= d.`valid_from` "
        "AND (f.`c3` < d.`valid_to` OR d.`valid_to` IS NULL) LIMIT 100"
    )
    assert len(result.physical_tables)==2
    assert result.compiler_version==2
    assert len(result.intent_sha256)==64
    assert compile_with()==result


def test_half_open_semantics_for_exact_end_boundary():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    end=datetime(2026,2,1,tzinfo=timezone.utc)
    in_interval=lambda moment,upper:start<=moment and (upper is None or moment<upper)
    assert in_interval(start,end)
    assert not in_interval(end,end)
    assert in_interval(end,None)


def test_modified_receipt_changes_intent_without_changing_sql():
    d,b,s,f,dim=inputs()
    changed=replace(dim,serving_materialization_id=999)
    models=(b.models[0],replace(b.models[1],serving_materialization_id=999))
    outcome=compile_with(draft=d,binding=replace(b,models=models),
                         specification=s,fact=f,dimension=changed)
    original=compile_with()
    assert outcome.sql==original.sql
    assert outcome.intent_sha256!=original.intent_sha256
    assert outcome.request_token!=original.request_token


@pytest.mark.parametrize("edit",[
    lambda d:d.replace(" >= "," > "),
    lambda d:d.replace(" < "," <= "),
    lambda d:d.replace(" OR "," AND "),
    lambda d:d.replace("d.valid_to IS NULL","d.valid_from IS NULL"),
    lambda d:d.replace(" LIMIT 100"," LIMIT 501"),
    lambda d:d.replace(" LIMIT 100"," WHERE f.amount > 0 LIMIT 100"),
    lambda d:d.replace(" INNER JOIN "," LEFT JOIN "),
])
def test_temporal_weakening_or_extra_sql_rejected(edit):
    d,b,s,f,dim=inputs()
    with pytest.raises(Exception):
        compile_with(draft=replace(d,normalized_sql=edit(d.normalized_sql)))


def test_altered_publication_pin_rejected():
    d,b,s,f,dim=inputs()
    wrong=replace(b,models=(replace(b.models[0],publication_revision=3),b.models[1]))
    with pytest.raises(ValueError):
        compile_with(binding=wrong)


def test_incomplete_or_wrong_business_key_types_rejected():
    d,b,s,f,dim=inputs()
    bad=replace(dim,business_keys=("product_id",))
    with pytest.raises(ValueError):
        compile_with(dimension=bad)
    bad=replace(dim,columns=(
        *dim.columns[:-1],("store_id","c4","INT64"),
    ))
    with pytest.raises(ValueError):
        compile_with(dimension=bad)


def test_temporal_upper_bound_cannot_be_rewritten():
    d,b,s,f,dim=inputs()
    from dataclasses import replace as r
    altered=r(s,temporal=r(s.temporal,upper_exclusive=False))
    with pytest.raises(ValueError):
        compile_with(specification=altered)


def test_missing_event_time_or_source_timezone_semantics_fail_closed():
    d,b,s,f,dim=inputs()
    without=replace(f,event_time_column=None,event_time_interpretation=None)
    with pytest.raises(ValueError):
        compile_with(fact=without)
    localized=replace(f,event_time_interpretation="SOURCE_TIMEZONE",
                      event_time_timezone="Asia/Kolkata")
    with pytest.raises(ValueError):
        compile_with(fact=localized)


def test_physical_column_drift_rejected():
    d,b,s,f,dim=inputs()
    mutated=replace(b,columns=(
        replace(b.columns[0],physical_name="c9"),b.columns[1],
    ))
    with pytest.raises(ValueError):
        compile_with(binding=mutated)


def test_no_cloud_db_or_execution_calls():
    path=Path(__file__).parents[1]/"app/warehouse/sql_scd2_join_compiler.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(n and n.startswith(("app.db","app.services","google","psycopg"))
                   for n in imports)
    assert not any(n.name in ("execute","reserve","claim")
                   for n in ast.walk(tree) if isinstance(n,ast.FunctionDef))
