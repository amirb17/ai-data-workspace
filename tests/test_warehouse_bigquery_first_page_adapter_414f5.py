"""414F5 strict BigQuery getQueryResults first-page adapter, no GCP."""
from copy import deepcopy
import pytest

from app.warehouse.query_bigquery_first_page_adapter import (
    validate_bigquery_first_page,BigQueryResultShapeError,
)
from app.warehouse.query_result_pages import ResultPageConflict

PROJECT="synthetic-project"
LOCATION="asia-south1"
JOB="dr_query_w5_r7_"+"a"*32
SCHEMA=(("id","INTEGER"),("cost","NUMERIC"),("active","BOOLEAN"),("name","STRING"))


def raw():
    return {
        "kind":"bigquery#getQueryResultsResponse",
        "jobReference":{"projectId":PROJECT,"location":LOCATION,"jobId":JOB},
        "jobComplete":True,
        "schema":{"fields":[
            {"name":"id","type":"INT64","mode":"NULLABLE"},
            {"name":"cost","type":"NUMERIC","mode":"NULLABLE"},
            {"name":"active","type":"BOOL","mode":"NULLABLE"},
            {"name":"name","type":"STRING","mode":"NULLABLE"},
        ]},
        "rows":[{"f":[{"v":"7"},{"v":"12.50"},{"v":"true"},{"v":"Coffee"}]}],
        "totalRows":"1",
    }


def parse(payload=None,rows=10):
    return validate_bigquery_first_page(
        project=PROJECT,location=LOCATION,job_id=JOB,
        approved_schema=SCHEMA,requested_rows=rows,
        raw=raw() if payload is None else payload,
    )


def test_bq_rest_scalar_types_are_converted_without_floating_precision_loss():
    result=parse()
    assert result.rows==((7,"12.50",True,"Coffee"),)
    assert result.job_id==JOB and result.next_page_token is None


@pytest.mark.parametrize("mutation",[
    lambda r:r.update(jobComplete=False),
    lambda r:r.update(errors=[{"reason":"stopped"}]),
    lambda r:r["jobReference"].update(jobId="other"),
    lambda r:r["jobReference"].update(projectId="foreign"),
    lambda r:r["schema"]["fields"][1].update(type="RECORD"),
    lambda r:r["schema"]["fields"][0].update(name="injected"),
    lambda r:r["schema"]["fields"][0].update(mode="REPEATED"),
    lambda r:r["rows"][0]["f"][0].update(v="7.5"),
    lambda r:r["rows"][0]["f"][1].update(v="NaN"),
    lambda r:r["rows"][0]["f"][2].update(v="TRUE"),
    lambda r:r["rows"][0]["f"][3].update(v={"nested":"object"}),
    lambda r:r.update(unreviewed={"payload":"ignored"}),
    lambda r:r.update(pageToken=7),
    lambda r:r["rows"][0].update(extra="new"),
])
def test_invalid_provider_pages_fail_closed(mutation):
    result=raw();mutation(result)
    with pytest.raises((BigQueryResultShapeError,ResultPageConflict)):
        parse(result)


def test_page_row_limit_rejects_provider_overdelivery():
    result=raw()
    result["rows"]*=2
    with pytest.raises(ResultPageConflict,match="ROW_LIMIT"):
        parse(result,rows=1)


def test_absent_rows_are_empty_success_and_cursor_is_internal():
    result=raw()
    result["rows"]=[]
    result["pageToken"]="next-private-page"
    page=parse(result)
    assert page.rows==()
    assert page.next_page_token=="next-private-page"


def test_large_provider_payload_denied_before_conversion():
    result=raw()
    result["rows"]=[{"f":[{"v":"7"},{"v":"1"},{"v":"true"},{"v":"x"*8192}]}]*50
    # Request enough rows that this specifically exercises the byte budget,
    # independently of the new earlier provider-overdelivery guard.
    with pytest.raises(BigQueryResultShapeError,match="TOO_LARGE"):
        parse(result,rows=100)


def test_non_finite_float_is_rejected():
    result=raw()
    result["schema"]["fields"][0]["type"]="FLOAT64"
    with pytest.raises(BigQueryResultShapeError):
        validate_bigquery_first_page(
            project=PROJECT,location=LOCATION,job_id=JOB,
            approved_schema=(("id","FLOAT"),*SCHEMA[1:]),
            requested_rows=10,raw={**result,"rows":[{"f":[{"v":"NaN"},{"v":"1"},{"v":"true"},{"v":"x"}]}]},
        )
