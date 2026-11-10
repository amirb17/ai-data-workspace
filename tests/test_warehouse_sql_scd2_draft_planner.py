"""Step 408C2: exact SCD2 predicates, no physical SQL execution."""
import pytest

from app.processing.sql_validation import SqlBlocked
from app.warehouse.sql_scd2_specification import scd2_join_specification
from app.warehouse.sql_scd2_draft_planner import validate_scd2_sql_draft
from tests.test_warehouse_sql_scd2_specification import contracts


def ctx():
    relation,fact,fs,dimension,ds=contracts()
    spec=scd2_join_specification(relation,fact,fs,dimension,ds)
    catalog={"workspace_id":7,"models":[
        {"model_id":12,"model_version_id":44,"model_type":"FACT","status":"APPROVED",
         "columns":[{"name":name,"role":"ATTRIBUTE"} for name in
                    ("line_id","product_id","store_id","amount","purchased_at")]},
        {"model_id":20,"model_version_id":55,"model_type":"DIMENSION","status":"APPROVED",
         "columns":[{"name":name,"role":"ATTRIBUTE"} for name in
                    ("product_id","store_id","category","changed_at")]},
    ]}
    return spec,catalog


BASE=("SELECT f.amount, d.category FROM model_12 AS f "
      "INNER JOIN model_20 AS d ON f.product_id = d.product_id "
      "AND f.store_id = d.store_id ")
LOWER="AND f.purchased_at >= d.valid_from "
UPPER="AND (f.purchased_at < d.valid_to OR d.valid_to IS NULL) "
GOOD=BASE+LOWER+UPPER+"LIMIT 100"


def draft(sql=GOOD,spec=None,catalog=None):
    initial,c=ctx()
    return validate_scd2_sql_draft(sql,specification=initial if spec is None else spec,
                                   catalog=c if catalog is None else catalog)


def test_exact_half_open_temporal_join_is_non_executable():
    result=draft()
    assert result.executable is False
    assert result.row_limit==100
    assert result.projected_columns==(("f","amount"),("d","category"))
    assert result.workspace_id==7
    assert (result.fact_model_id,result.fact_model_version_id)==(12,44)
    assert (result.dimension_model_id,result.dimension_model_version_id)==(20,55)
    assert "model_12" in result.normalized_sql
    assert "model_20" in result.normalized_sql


def test_equivalent_key_and_upper_clause_order():
    sql=("SELECT d.category FROM model_12 AS f INNER JOIN model_20 AS d "
         "ON d.store_id = f.store_id AND d.product_id = f.product_id "
         "AND (d.valid_to IS NULL OR f.purchased_at < d.valid_to) "
         "AND f.purchased_at >= d.valid_from LIMIT 5")
    assert draft(sql).row_limit==5


@pytest.mark.parametrize("sql",[
    BASE+"LIMIT 100",
    BASE+LOWER+"LIMIT 100",
    BASE+UPPER+"LIMIT 100",
    BASE+"AND f.purchased_at > d.valid_from "+UPPER+"LIMIT 100",
    BASE+"AND f.purchased_at <= d.valid_from "+UPPER+"LIMIT 100",
    BASE+LOWER+"AND (f.purchased_at <= d.valid_to OR d.valid_to IS NULL) LIMIT 100",
    BASE+LOWER+"AND (f.purchased_at < d.valid_to OR d.valid_to IS NOT NULL) LIMIT 100",
    BASE+LOWER+"AND (f.purchased_at < d.valid_to OR d.valid_from IS NULL) LIMIT 100",
    BASE+LOWER+"AND (f.purchased_at < d.valid_to OR TRUE) LIMIT 100",
    BASE+LOWER+"AND f.purchased_at < d.valid_to LIMIT 100",
    BASE+LOWER+"AND d.valid_to IS NULL LIMIT 100",
    BASE+LOWER+"AND (f.purchased_at < d.valid_to OR d.valid_to IS NULL OR f.amount>0) LIMIT 100",
    BASE+LOWER+UPPER+"AND f.amount > 0 LIMIT 100",
    BASE+LOWER+UPPER+"WHERE d.category='x' LIMIT 100",
    BASE+LOWER+UPPER+"ORDER BY d.category LIMIT 100",
    BASE+LOWER+UPPER+"LIMIT 0",
    BASE+LOWER+UPPER+"LIMIT 501",
    BASE+LOWER+UPPER.replace("f.purchased_at","d.changed_at")+"LIMIT 100",
    BASE.replace("f.store_id = d.store_id ","")+" "+LOWER+UPPER+"LIMIT 100",
    BASE.replace("f.product_id = d.product_id","f.product_id = d.store_id")+LOWER+UPPER+"LIMIT 100",
    GOOD.replace("d.category","d.unknown"),
    GOOD.replace("f.amount","amount"),
    GOOD.replace("INNER JOIN","LEFT JOIN"),
    GOOD.replace("model_20 AS d","external.dataset.model_20 AS d"),
    GOOD+"; DELETE FROM model_20",
])
def test_missing_weakened_reversed_or_extra_predicates_rejected(sql):
    with pytest.raises(SqlBlocked):
        draft(sql)


def test_wrong_catalog_scope_version_and_status_rejected():
    spec,catalog=ctx()
    with pytest.raises(SqlBlocked,match="SCOPE"):
        draft(spec=spec,catalog={**catalog,"workspace_id":99})
    old={**catalog["models"][1],"model_version_id":99}
    with pytest.raises(SqlBlocked):
        draft(spec=spec,catalog={**catalog,"models":[catalog["models"][0],old]})
    archived={**catalog["models"][1],"status":"ARCHIVED"}
    with pytest.raises(SqlBlocked):
        draft(spec=spec,catalog={**catalog,"models":[catalog["models"][0],archived]})


def test_time_contract_cannot_be_weakened():
    from dataclasses import replace
    spec,_=ctx()
    with pytest.raises(SqlBlocked):
        draft(spec=replace(spec,temporal=replace(spec.temporal,upper_exclusive=False)))
    with pytest.raises(SqlBlocked):
        draft(spec=replace(spec,temporal=replace(spec.temporal,null_upper_is_unbounded=False)))


def test_no_provider_or_db_imports():
    import ast
    from pathlib import Path
    source=Path(__file__).parents[1]/"app/warehouse/sql_scd2_draft_planner.py"
    tree=ast.parse(source.read_text(encoding="utf-8"))
    imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(n and n.startswith(("app.db","google","boto3","psycopg")) for n in imports)
