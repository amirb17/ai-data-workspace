"""STRING warehouse result support remains strictly bounded and read-only."""
import pytest

from app.warehouse.named_query_results import validate_fixed_schema
from app.warehouse.query_bigquery_first_page_adapter import (
    BigQueryResultShapeError, validate_bigquery_first_page,
)


def _page(value):
    return {
        "jobReference": {
            "projectId": "datarise-511817",
            "location": "asia-south1",
            "jobId": "dr_query_w18_r7_" + "a" * 32,
        },
        "jobComplete": True,
        "schema": {"fields": [
            {"name": "customer_name", "type": "STRING", "mode": "NULLABLE"},
        ]},
        "rows": [{"f": [{"v": value}]}],
    }


def _read(value):
    return validate_bigquery_first_page(
        project="datarise-511817",
        location="asia-south1",
        job_id="dr_query_w18_r7_" + "a" * 32,
        approved_schema=(("customer_name", "STRING"),),
        requested_rows=1,
        raw=_page(value),
    )


def test_bounded_warehouse_customer_string_column_is_supported():
    validate_fixed_schema([("customer_name", "STRING")], 100)
    assert _read("Pune").rows == (("Pune",),)


def test_string_result_rejects_oversized_or_nested_values():
    for cell in ("x" * 8193, {"nested": "value"}, 3.14):
        with pytest.raises(BigQueryResultShapeError):
            _read(cell)


def test_string_does_not_permit_unbounded_schema_or_row_count():
    with pytest.raises(ValueError):
        validate_fixed_schema([("customer_name", "STRING")], 501)
    with pytest.raises(ValueError):
        validate_fixed_schema([("customer_name", "JSON")], 100)
    with pytest.raises(ValueError):
        validate_fixed_schema([("customer_name", "STRING")] * 33, 100)


def test_large_encoded_response_still_rejected():
    with pytest.raises(BigQueryResultShapeError, match="PROVIDER_RESPONSE_TOO_LARGE"):
        validate_bigquery_first_page(
            project="datarise-511817",
            location="asia-south1",
            job_id="dr_query_w18_r7_" + "a" * 32,
            approved_schema=tuple((f"col_{i}", "STRING") for i in range(32)),
            requested_rows=100,
            raw={
                "jobReference": _page("x")["jobReference"],
                "jobComplete": True,
                "schema": {"fields": [
                    {"name": f"col_{i}", "type": "STRING"} for i in range(32)
                ]},
                "rows": [
                    {"f": [{"v": "x" * 512} for _ in range(32)]}
                    for _ in range(30)
                ],
            },
        )
