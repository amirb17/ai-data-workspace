"""P0 bounded warehouse SQL and BigQuery first-page adversarial checks.

No cloud IO and no million-row allocations are required.
"""
from copy import deepcopy

import pytest

from app.processing.sql_validation import SqlBlocked
from app.warehouse.sql_draft_planner import plan_draft
from app.warehouse.query_bigquery_first_page_adapter import (
    BigQueryResultShapeError,validate_bigquery_first_page,
)
from tests.test_warehouse_bigquery_first_page_adapter_414f5 import (
    PROJECT,LOCATION,JOB,SCHEMA,raw,
)
from tests.test_warehouse_sql_draft_planner import CATALOG


@pytest.mark.parametrize("sql",[
    "SELECT * FROM model_12",
    "SELECT * FROM model_12 LIMIT 5",
    "SELECT amount FROM model_12",
    "SELECT amount FROM model_12 LIMIT 1000000",
    "SELECT amount FROM model_12 LIMIT 501",
    "SELECT amount FROM model_12 LIMIT 500 UNION ALL SELECT amount FROM model_12 LIMIT 500",
    "SELECT amount FROM (SELECT amount FROM model_12 LIMIT 1) LIMIT 5",
    "SELECT amount FROM model_12 LIMIT 1 OFFSET 1000000",
])
def test_unsafe_or_unbounded_logical_sql_never_compiles(sql):
    with pytest.raises(SqlBlocked):
        plan_draft(sql,workspace_id=7,catalog=CATALOG)


def parse(response,requested_rows=100):
    return validate_bigquery_first_page(
        project=PROJECT,location=LOCATION,job_id=JOB,
        approved_schema=SCHEMA,requested_rows=requested_rows,raw=response,
    )


def test_fake_provider_one_million_overdelivery_rejected_without_allocation():
    result=raw()
    # One shared object repeated 100k times simulates provider overdelivery;
    # do not allocate large scalar payloads.
    result["rows"]=[result["rows"][0]]*100_000
    with pytest.raises(BigQueryResultShapeError,match="ROW_LIMIT_EXCEEDED"):
        parse(result)


def test_provider_extra_rows_fail_before_cell_conversion():
    result=raw()
    result["rows"]=[{"f":"invalid shape"}]*101
    with pytest.raises(BigQueryResultShapeError,match="ROW_LIMIT_EXCEEDED"):
        parse(result)


def test_oversized_raw_provider_field_bounded_without_full_json_dump(monkeypatch):
    result=raw()
    result["etag"]="a"*(256*1024+1)
    # Prevent a future regression to building a full duplicate JSON byte copy.
    def no_dumps(*args,**kwargs):
        raise AssertionError("json.dumps should not run on oversized provider response")
    monkeypatch.setattr("app.warehouse.query_bigquery_first_page_adapter.json.dumps",no_dumps)
    with pytest.raises(BigQueryResultShapeError,match="PROVIDER_RESPONSE_TOO_LARGE"):
        parse(result)


def test_nonfinite_or_nested_raw_provider_values_fail_closed():
    result=raw()
    result["totalBytesProcessed"]=float("nan")
    with pytest.raises(BigQueryResultShapeError,match="INVALID_PROVIDER_RESPONSE"):
        parse(result)
    nested=raw()
    nested["rows"][0]["f"][3]["v"]={"nested":True}
    with pytest.raises(BigQueryResultShapeError):
        parse(nested)


def test_bounded_first_page_preserves_valid_rows():
    result=raw()
    page=parse(result,requested_rows=1)
    assert page.rows==((7,"12.50",True,"Coffee"),)
    assert page.bytes_encoded<=256*1024
