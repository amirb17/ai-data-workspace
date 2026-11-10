"""Step 408B: only exact reviewed SCD1 join drafts are allowed."""
import pytest
from app.processing.sql_validation import SqlBlocked
from app.warehouse.sql_join_draft_planner import plan_join_draft

CATALOG={"workspace_id":7,"models":[
    {"model_id":12,"model_version_id":44,"model_type":"FACT","status":"APPROVED",
     "columns":[{"name":"product_id","role":"ATTRIBUTE"},{"name":"store_id","role":"ATTRIBUTE"},
                {"name":"amount","role":"MEASURE"}]},
    {"model_id":20,"model_version_id":55,"model_type":"DIMENSION","status":"APPROVED",
     "columns":[{"name":"product_id","role":"IDENTITY"},{"name":"store_id","role":"IDENTITY"},
                {"name":"category","role":"ATTRIBUTE"}]},
]}
RELATION={"relationship_id":9,"workspace_id":7,
          "fact_model_id":12,"fact_model_version_id":44,
          "dimension_model_id":20,"dimension_model_version_id":55,
          "cardinality":"MANY_TO_ONE","temporal_policy":"CURRENT_MEMBER",
          "key_pairs":[{"fact_column":"product_id","dimension_column":"product_id"},
                       {"fact_column":"store_id","dimension_column":"store_id"}]}
GOOD=("SELECT f.amount, d.category FROM model_12 AS f "
      "INNER JOIN model_20 AS d "
      "ON f.product_id = d.product_id AND f.store_id = d.store_id LIMIT 100")


def plan(sql=GOOD, catalog=CATALOG, relationships=None, relationship_id=9, workspace_id=7):
    return plan_join_draft(sql,workspace_id=workspace_id,catalog=catalog,
                           relationships=[RELATION] if relationships is None else relationships,
                           relationship_id=relationship_id)


def test_reviewed_exact_composite_join_draft():
    result=plan()
    assert result.relationship_id==9
    assert (result.fact_model_id,result.fact_model_version_id)==(12,44)
    assert (result.dimension_model_id,result.dimension_model_version_id)==(20,55)
    assert result.projected_columns==(("f","amount"),("d","category"))
    assert result.row_limit==100 and result.executable is False
    assert "model_12" in result.normalized_sql and "model_20" in result.normalized_sql


def test_reversed_equality_order_remains_exact():
    sql=("SELECT f.amount FROM model_12 AS f INNER JOIN model_20 AS d "
         "ON d.store_id = f.store_id AND d.product_id = f.product_id LIMIT 3")
    assert plan(sql).row_limit==3


@pytest.mark.parametrize("sql",[
    "SELECT f.amount FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.product_id LIMIT 5",
    "SELECT f.amount FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.store_id AND f.store_id=d.product_id LIMIT 5",
    "SELECT f.amount FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.product_id AND f.product_id=d.product_id LIMIT 5",
    "SELECT f.amount FROM model_12 AS f LEFT JOIN model_20 AS d ON f.product_id=d.product_id AND f.store_id=d.store_id LIMIT 5",
    "SELECT f.amount FROM model_20 AS d JOIN model_12 AS f ON f.product_id=d.product_id AND f.store_id=d.store_id LIMIT 5",
    "SELECT amount FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.product_id AND f.store_id=d.store_id LIMIT 5",
    "SELECT f.* FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.product_id AND f.store_id=d.store_id LIMIT 5",
    "SELECT f.amount FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.product_id AND f.store_id=d.store_id WHERE f.amount>0 LIMIT 5",
    "SELECT COUNT(*) FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.product_id AND f.store_id=d.store_id LIMIT 5",
    "SELECT f.amount FROM model_12 AS f JOIN model_20 AS d USING(product_id,store_id) LIMIT 5",
    "SELECT f.amount FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.product_id AND f.store_id=d.store_id LIMIT 0",
    "SELECT f.amount FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.product_id AND f.store_id=d.store_id LIMIT 501",
    "SELECT f.amount FROM model_12 AS f JOIN model_20 AS d ON f.product_id=d.product_id AND f.store_id=d.store_id",
    "SELECT f.amount FROM model_12 AS f JOIN project.dataset.model_20 AS d ON f.product_id=d.product_id AND f.store_id=d.store_id LIMIT 5",
])
def test_unreviewed_or_unsupported_sql_fails_closed(sql):
    with pytest.raises(SqlBlocked):
        plan(sql)


@pytest.mark.parametrize("update",[
    {"workspace_id":8},
    {"fact_model_version_id":999},
    {"dimension_model_version_id":999},
    {"temporal_policy":"EVENT_TIME_AS_OF"},
    {"cardinality":"MANY_TO_MANY"},
    {"key_pairs":[]},
    {"key_pairs":[{"fact_column":"wrong","dimension_column":"product_id"}]},
])
def test_invalid_relationship_or_stale_pins_block(update):
    with pytest.raises(SqlBlocked):
        plan(relationships=[{**RELATION,**update}])


def test_unknown_relationship_and_foreign_catalog_fail_closed():
    with pytest.raises(SqlBlocked):
        plan(relationship_id=10)
    with pytest.raises(SqlBlocked):
        plan(catalog={**CATALOG,"workspace_id":99})
    with pytest.raises(SqlBlocked):
        plan(relationships=[])


def test_unknown_or_archived_models_block():
    with pytest.raises(SqlBlocked):
        plan(catalog={**CATALOG,"models":CATALOG["models"][:1]})
    stale={**CATALOG["models"][1],"status":"ARCHIVED"}
    with pytest.raises(SqlBlocked):
        plan(catalog={**CATALOG,"models":[CATALOG["models"][0],stale]})


def test_no_cloud_or_database_imports():
    import ast
    from pathlib import Path
    tree=ast.parse((Path(__file__).parents[1]/"app/warehouse/sql_join_draft_planner.py").read_text())
    names=[x.module for x in ast.walk(tree) if isinstance(x,ast.ImportFrom)]
    assert not any(n and n.startswith(("app.db","google","boto3","psycopg")) for n in names)
