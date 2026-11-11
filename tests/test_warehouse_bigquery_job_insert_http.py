"""Offline acceptance for opt-in single-attempt BigQuery submission transport.

No ADC, network, cloud credentials or production runtime are used.
"""
import copy

import pytest
import requests

from app.warehouse.query_bigquery_job_insert_http import (
    BoundedBigQueryJobInsertTransport,
)
from app.warehouse.query_submission_transport import (
    submit_exact_job_attempt_after_boundary,
)
from app.warehouse.query_job_request import BigQueryJobRequest

PROJECT = "datarise-511817"
JOB = "dr_query_w18_r7_" + "a" * 32


class Response:
    status_code = 200
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/jobs"
    headers = {"content-type": "application/json"}
    def close(self):
        self.closed = True


def request_body():
    return {
        "jobReference": {
            "projectId": PROJECT, "location": "asia-south1", "jobId": JOB,
        },
        "configuration": {
            "jobType": "QUERY", "dryRun": False, "jobTimeoutMs": "30000",
            "labels": {
                "datarise": "query", "workspace": "18",
                "reservation": "7", "intent": "b" * 63,
            },
            "query": {
                "query": "SELECT value FROM `datarise-511817.dr_dev_w18.t` LIMIT 1",
                "useLegacySql": False, "useQueryCache": False,
                "priority": "INTERACTIVE", "allowLargeResults": False,
                "maximumBytesBilled": "1048576",
                "destinationTable": {
                    "projectId": PROJECT, "datasetId": "dr_results_dev_w18",
                    "tableId": "result_" + "c" * 64,
                },
                "createDisposition": "CREATE_IF_NEEDED",
                "writeDisposition": "WRITE_EMPTY",
            },
        },
    }


def prepared():
    s = requests.Session()
    s.trust_env = False
    s.headers["Authorization"] = "Bearer " + "x" * 32
    return s


def test_single_post_and_ack_still_unverified(monkeypatch):
    session = prepared()
    calls = []
    response = Response()
    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return response
    monkeypatch.setattr(session, "request", fake_request)
    transport = BoundedBigQueryJobInsertTransport(
        project=PROJECT, location="asia-south1", session=session,
    )
    request = BigQueryJobRequest(PROJECT, "asia-south1", JOB, request_body())
    observation = submit_exact_job_attempt_after_boundary(
        request=request, transport=transport,
    )
    assert observation.classification == "INSERT_ACKNOWLEDGED_UNVERIFIED"
    assert not observation.verified and not observation.execution_authorized
    assert len(calls) == 1 and calls[0][0] == "POST"
    assert calls[0][2]["allow_redirects"] is False
    assert calls[0][2]["stream"] is True
    assert response.closed


@pytest.mark.parametrize("mutation", [
    lambda b: b["configuration"]["query"].update(maximumBytesBilled="268435457"),
    lambda b: b["configuration"]["query"].update(useQueryCache=True),
    lambda b: b["configuration"]["query"].update(writeDisposition="WRITE_TRUNCATE"),
    lambda b: b["configuration"]["query"]["destinationTable"].update(datasetId="dr_dev_w18"),
    lambda b: b["configuration"]["query"].pop("destinationTable"),
    lambda b: b["configuration"].update(dryRun=True),
    lambda b: b["configuration"].update(jobTimeoutMs="300001"),
    lambda b: b["jobReference"].update(location="US"),
])
def test_invalid_request_never_reaches_provider(monkeypatch, mutation):
    session = prepared()
    calls = []
    monkeypatch.setattr(session, "request", lambda *a, **kw: calls.append(a))
    transport = BoundedBigQueryJobInsertTransport(
        project=PROJECT, location="asia-south1", session=session,
    )
    body = copy.deepcopy(request_body())
    mutation(body)
    with pytest.raises(ValueError):
        transport.insert_job_once(project=PROJECT, location="asia-south1", body=body)
    assert not calls


@pytest.mark.parametrize("status", [301, 403, 409, 500])
def test_non_200_is_ambiguous_and_single_attempt(monkeypatch, status):
    session = prepared()
    calls = []
    response = Response()
    response.status_code = status
    def fake(*a, **kw):
        calls.append(1)
        return response
    monkeypatch.setattr(session, "request", fake)
    transport = BoundedBigQueryJobInsertTransport(
        project=PROJECT, location="asia-south1", session=session,
    )
    result = submit_exact_job_attempt_after_boundary(
        request=BigQueryJobRequest(PROJECT, "asia-south1", JOB, request_body()),
        transport=transport,
    )
    assert result.classification == "INSERT_OUTCOME_UNKNOWN"
    assert len(calls) == 1 and response.closed


def test_no_bearer_or_environment_trust_rejected():
    s = requests.Session()
    with pytest.raises(ValueError):
        BoundedBigQueryJobInsertTransport(
            project=PROJECT, location="asia-south1", session=s,
        )
    s = prepared()
    s.trust_env = True
    with pytest.raises(ValueError):
        BoundedBigQueryJobInsertTransport(
            project=PROJECT, location="asia-south1", session=s,
        )
