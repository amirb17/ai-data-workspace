"""414F5 exact read-only BigQuery GET boundary, injected fake connection."""
import pytest

from app.warehouse.query_bigquery_readonly_transport import (
    ReadOnlyBigQueryFirstPage, ReviewedResultsConnection,
)
from app.warehouse.query_bigquery_first_page_adapter import (
    BigQueryResultShapeError,validate_bigquery_first_page,
)
from tests.test_warehouse_bigquery_first_page_adapter_414f5 import (
    PROJECT, LOCATION, JOB, SCHEMA, raw,
)


class FakeConnection:
    def __init__(self, payload=None, error=None):
        self.calls=[]
        self.payload=raw() if payload is None else payload
        self.error=error

    def api_request(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.payload


def transport(connection):
    return ReadOnlyBigQueryFirstPage(ReviewedResultsConnection(
        PROJECT,LOCATION,connection,
    ))


def get(connection,**overrides):
    params=dict(project=PROJECT,location=LOCATION,job_id=JOB,max_results=10)
    params.update(overrides)
    return transport(connection).get_query_results_once(**params)


def test_exact_job_results_get_once_no_retry_and_no_job_submission():
    fake=FakeConnection()
    response=get(fake)
    assert len(fake.calls)==1
    assert fake.calls[0]=={
        "method":"GET",
        "path":f"/projects/{PROJECT}/queries/{JOB}",
        "query_params":{
            "location":LOCATION,"maxResults":10,"timeoutMs":1000,
        },
        "timeout":10,"retry":None,
    }
    assert validate_bigquery_first_page(
        project=PROJECT,location=LOCATION,job_id=JOB,
        approved_schema=SCHEMA,requested_rows=10,raw=response,
    ).rows==((7,"12.50",True,"Coffee"),)


@pytest.mark.parametrize("overrides",[
    {"project":"foreign-project"},
    {"location":"US"},
    {"job_id":"other"},
    {"job_id":"dr_query_w5_r8_"+"b"*32,"max_results":101},
    {"max_results":0},
    {"max_results":101},
    {"max_results":True},
])
def test_request_scope_or_page_limit_rejected_before_network(overrides):
    fake=FakeConnection()
    with pytest.raises(ValueError,match="ORIGINAL_BOUNDED"):
        get(fake,**overrides)
    assert fake.calls==[]


def test_connection_failure_redacts_error_without_another_request():
    fake=FakeConnection(error=TimeoutError("secret provider diagnostic"))
    with pytest.raises(BigQueryResultShapeError,match="RESULT_PROVIDER_READ_UNAVAILABLE") as exc:
        get(fake)
    assert "secret" not in str(exc.value)
    assert len(fake.calls)==1


def test_invalid_connection_and_provider_payload_fail_closed():
    with pytest.raises(ValueError):
        ReviewedResultsConnection(PROJECT,LOCATION,object())
    fake=FakeConnection(payload="bad provider payload")
    with pytest.raises(BigQueryResultShapeError,match="INVALID_PROVIDER_RESPONSE"):
        get(fake)
    assert len(fake.calls)==1


def test_explicit_connection_has_no_client_factory_or_sql_submission():
    fake=FakeConnection()
    adapter=transport(fake)
    assert not hasattr(adapter,"submit")
    assert not hasattr(adapter,"query")
    assert not hasattr(adapter,"cancel")
    assert not fake.calls
