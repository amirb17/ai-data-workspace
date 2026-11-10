"""Step 408A: strict non-executable warehouse SQL draft grammar."""
import pytest

from app.processing.sql_validation import SqlBlocked
from app.warehouse.sql_draft_planner import plan_draft


CATALOG = {
    "workspace_id": 7,
    "models": [{
        "model_id": 12,
        "model_version_id": 44,
        "model_type": "FACT",
        "status": "APPROVED",
        "columns": [
            {"name": "line_id", "type": "STRING", "role": "IDENTITY"},
            {"name": "amount", "type": "DECIMAL", "role": "MEASURE"},
            {"name": "product_id", "type": "STRING", "role": "ATTRIBUTE"},
        ],
        "queryable": False,
    }],
}


def draft(sql, catalog=CATALOG, workspace_id=7):
    return plan_draft(sql,workspace_id=workspace_id,catalog=catalog)


def test_one_logical_model_projection_is_non_executable():
    result=draft("SELECT line_id, amount FROM model_12 LIMIT 100")
    assert result.model_id==12 and result.model_version_id==44
    assert result.projected_columns==("line_id","amount")
    assert result.row_limit==100
    assert result.executable is False
    assert "model_12" in result.normalized_sql
    assert "synthetic-project" not in result.normalized_sql


def test_alias_qualified_columns_only():
    result=draft("SELECT f.line_id, f.product_id FROM model_12 AS f LIMIT 5")
    assert result.projected_columns==("line_id","product_id")


@pytest.mark.parametrize("sql",[
    "SELECT * FROM model_12 LIMIT 10",
    "SELECT amount FROM model_12",
    "SELECT amount FROM model_12 LIMIT 0",
    "SELECT amount FROM model_12 LIMIT 501",
    "SELECT COUNT(*) FROM model_12 LIMIT 10",
    "SELECT SUM(amount) FROM model_12 LIMIT 10",
    "SELECT amount + 1 FROM model_12 LIMIT 10",
    "SELECT amount FROM model_12 WHERE amount > 0 LIMIT 10",
    "SELECT DISTINCT amount FROM model_12 LIMIT 10",
    "SELECT amount FROM model_12 ORDER BY amount LIMIT 10",
    "SELECT amount FROM model_12 GROUP BY amount LIMIT 10",
    "SELECT amount FROM model_12 OFFSET 1 LIMIT 10",
    "SELECT amount FROM model_12 JOIN model_12 AS t ON t.line_id = model_12.line_id LIMIT 10",
    "SELECT amount FROM external.dataset.model_12 LIMIT 10",
    "WITH data AS (SELECT amount FROM model_12) SELECT amount FROM data LIMIT 10",
    "SELECT amount FROM (SELECT amount FROM model_12) LIMIT 10",
    "SELECT amount FROM model_12 LIMIT 10; DELETE FROM model_12",
    "DROP TABLE model_12",
    "UPDATE model_12 SET amount=1",
    "SELECT secret FROM model_12 LIMIT 10",
    "SELECT amount, amount FROM model_12 LIMIT 10",
    "SELECT wrong.amount FROM model_12 LIMIT 10",
    "SELECT amount FROM model_999 LIMIT 10",
    "SELECT amount FROM model_12 LIMIT '3'",
])
def test_unsupported_or_unsafe_sql_is_rejected(sql):
    with pytest.raises(SqlBlocked):
        draft(sql)


def test_catalog_workspace_mismatch_rejected():
    with pytest.raises(SqlBlocked) as caught:
        draft("SELECT amount FROM model_12 LIMIT 10",workspace_id=8)
    assert caught.value.code=="SCOPE"


@pytest.mark.parametrize("changes",[
    {"status":"DRAFT"},
    {"model_type":"OTHER"},
    {"model_version_id":None},
    {"model_version_id":True},
])
def test_unapproved_model_metadata_rejected(changes):
    model={**CATALOG["models"][0],**changes}
    with pytest.raises(SqlBlocked):
        draft("SELECT amount FROM model_12 LIMIT 10",{**CATALOG,"models":[model]})


def test_missing_or_unapproved_column_rejected():
    model={**CATALOG["models"][0],"columns":[{"name":"amount","role":"NOT_APPROVED"}]}
    with pytest.raises(SqlBlocked):
        draft("SELECT amount FROM model_12 LIMIT 10",{**CATALOG,"models":[model]})


def test_duplicate_model_identity_rejected():
    with pytest.raises(SqlBlocked):
        draft("SELECT amount FROM model_12 LIMIT 10",
              {**CATALOG,"models":[CATALOG["models"][0],CATALOG["models"][0]]})


def test_empty_or_overlong_statement_rejected():
    for sql in ("", " " * 4097):
        with pytest.raises(SqlBlocked):
            draft(sql)


def test_draft_module_has_no_provider_or_db_dependency():
    import ast
    from pathlib import Path
    tree=ast.parse((Path(__file__).parents[1]/"app/warehouse/sql_draft_planner.py").read_text(encoding="utf-8"))
    names=[item.module for item in ast.walk(tree) if isinstance(item,ast.ImportFrom)]
    assert not any(name and name.startswith(("google","boto3","psycopg","app.db"))
                   for name in names)
