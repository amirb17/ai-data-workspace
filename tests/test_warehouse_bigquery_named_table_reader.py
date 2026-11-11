"""C8: independently authorized named-table data GET; never jobs.getQueryResults.

All requests simulated using an injected plain HTTP session. No ADC/network.
"""
from copy import deepcopy
import time

import pytest
import requests

from app.warehouse.named_query_results import (
    STORAGE_BYTES, WORKSPACE_STORAGE_BYTES, result_identity,
)
from app.warehouse.query_bigquery_first_page_adapter import (
    BigQueryResultShapeError, validate_bigquery_first_page,
)
from app.warehouse.query_bigquery_named_table_reader import NamedTableBigQueryResultReader

PROJECT = "datarise-511817"
JOB = "dr_query_w18_r7_" + "a" * 32
READER = "datarise-result-reader@datarise-511817.iam.gserviceaccount.com"
SENDER = "datarise-query-sender@datarise-511817.iam.gserviceaccount.com"


def expected():
    return dict(
        contract_version=2, project=PROJECT, dataset="dr_dev_w18",
        location="asia-south1", job_id=JOB,
        provider_policy=dict(creator_email=SENDER),
        result=dict(
            version=1, destination=result_identity(PROJECT, 18, JOB),
            schema=[["check_value", "INTEGER"]], maximum_rows=1,
            maximum_storage_bytes=STORAGE_BYTES,
            workspace_storage_bytes=WORKSPACE_STORAGE_BYTES,
            read_until_ms=int(time.time() * 1000) + 900000,
            policy=dict(reader_principal=READER, revision="c8-test",
                        read_seconds=3600),
        ),
    )


def session():
    value = requests.Session()
    value.trust_env = False
    value.headers["Authorization"] = "Bearer " + "x" * 32
    return value


class Response:
    status_code = 200
    headers = {"content-type": "application/json"}
    url = (
        "https://bigquery.googleapis.com/bigquery/v2/projects/"
        f"{PROJECT}/datasets/dr_results_dev_w18/tables/"
        f"{result_identity(PROJECT, 18, JOB)['tableId']}/data"
    )
    closed = False
    def iter_content(self, *, chunk_size):
        yield b'{"kind":"bigquery#tableDataList","totalRows":"1",'
        yield b'"rows":[{"f":[{"v":"1"}]}]}'
    def close(self):
        self.closed = True


def test_exact_independent_table_read_and_original_schema(monkeypatch):
    s = session()
    calls = []
    response = Response()
    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return response
    monkeypatch.setattr(s, "request", fake)
    reader = NamedTableBigQueryResultReader(
        expected=expected(), principal=READER, session=s
    )
    raw = reader.get_query_results_once(
        project=PROJECT, location="asia-south1", job_id=JOB, max_results=1
    )
    checked = validate_bigquery_first_page(
        project=PROJECT, location="asia-south1", job_id=JOB,
        approved_schema=(("check_value", "INTEGER"),), requested_rows=1,
        raw=raw,
    )
    assert checked.rows == ((1,),)
    assert len(calls) == 1
    assert calls[0][0] == "GET"
    assert "/tables/" in calls[0][1] and calls[0][1].endswith("/data")
    assert "/queries/" not in calls[0][1]
    assert calls[0][2]["allow_redirects"] is False
    assert calls[0][2]["stream"] is True
    assert response.closed


@pytest.mark.parametrize("change", [
    lambda e: e["result"]["destination"].update(datasetId="dr_dev_w18"),
    lambda e: e["result"]["policy"].update(reader_principal=SENDER),
    lambda e: e.update(location="US"),
])
def test_invalid_pinned_contract_fails_before_io(change):
    e = expected()
    change(e)
    with pytest.raises(ValueError):
        NamedTableBigQueryResultReader(expected=e, principal=READER, session=session())


def test_expired_window_never_sends(monkeypatch):
    e = expected()
    e["result"]["read_until_ms"] = 1
    s = session()
    calls = []
    monkeypatch.setattr(s, "request", lambda *a, **kw: calls.append(1))
    reader = NamedTableBigQueryResultReader(expected=e, principal=READER, session=s)
    with pytest.raises(ValueError):
        reader.get_query_results_once(
            project=PROJECT, location="asia-south1", job_id=JOB, max_results=1
        )
    assert calls == []


@pytest.mark.parametrize("status,body", [
    (403, b'{"error":"permission denied"}'),
    (200, b'{"rows":[{"f":[{"v":"1"}]},{"f":[{"v":"2"}]}]}'),
    (200, b'{"rows":[], "unexpected":"leak"}'),
])
def test_bad_provider_response_denied_and_closed(monkeypatch, status, body):
    class Bad(Response):
        status_code = status
        def iter_content(self, *, chunk_size):
            yield body
    s = session()
    response = Bad()
    monkeypatch.setattr(s, "request", lambda *a, **kw: response)
    reader = NamedTableBigQueryResultReader(
        expected=expected(), principal=READER, session=s
    )
    with pytest.raises(BigQueryResultShapeError):
        reader.get_query_results_once(
            project=PROJECT, location="asia-south1", job_id=JOB, max_results=1
        )
    assert response.closed
