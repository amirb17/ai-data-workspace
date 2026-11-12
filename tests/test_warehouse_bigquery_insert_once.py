"""A real jobs.insert adapter makes one call, never client.query or retry."""
from unittest.mock import Mock, patch
import pytest

from app.warehouse.query_bigquery_insert_once import (
    BigQueryJobInsertOnce, BigQueryInsertTransportError, submit_one_reviewed_job,
)
from app.warehouse.query_job_request import BigQueryJobRequest


def request():
    body={
        "jobReference":{
            "projectId":"datarise-511817",
            "location":"asia-south1",
            "jobId":"dr_query_w18_r9_"+"a"*32,
        },
        "configuration":{
            "jobType":"QUERY","dryRun":False,
            "query":{"query":"SELECT 1", "useLegacySql":False},
        },
    }
    return BigQueryJobRequest(
        "datarise-511817","asia-south1",body["jobReference"]["jobId"],body)


def client():
    c=Mock()
    c.project="datarise-511817"
    c._call_api.return_value={"jobReference":{"jobId":"x"}}
    return c


def test_real_adapter_posts_single_exact_job_without_any_retry():
    c=client()
    transport=BigQueryJobInsertOnce(c,"datarise-511817","asia-south1")
    job=request()
    observation=submit_one_reviewed_job(request=job,transport=transport)
    assert observation.job_id==job.job_id
    assert observation.lookup_required is True
    assert observation.verified is False
    assert observation.classification=="INSERT_ACKNOWLEDGED_UNVERIFIED"
    c._call_api.assert_called_once_with(
        retry=None, timeout=10, method="POST",
        path="/projects/datarise-511817/jobs", data=job.body,
    )
    c.query.assert_not_called()


def test_unknown_response_does_not_resubmit():
    c=client()
    c._call_api.side_effect=TimeoutError("unknown cloud response")
    observation=submit_one_reviewed_job(
        request=request(),
        transport=BigQueryJobInsertOnce(c,"datarise-511817","asia-south1"),
    )
    assert observation.classification=="INSERT_OUTCOME_UNKNOWN"
    assert observation.lookup_required is True
    assert c._call_api.call_count==1


def test_rejects_unscoped_clients_or_edited_request_before_network():
    c=client()
    with pytest.raises(BigQueryInsertTransportError):
        BigQueryJobInsertOnce(c,"other-project","asia-south1")
    transport=BigQueryJobInsertOnce(c,"datarise-511817","asia-south1")
    job=request()
    edited=dict(job.body)
    edited["jobReference"]=dict(edited["jobReference"],location="us-central1")
    with pytest.raises(BigQueryInsertTransportError):
        transport.insert_job_once(project=job.project, location=job.location,
                                  body=edited)
    c._call_api.assert_not_called()


def test_request_for_other_project_is_blocked_before_cloud():
    c=client()
    transport=BigQueryJobInsertOnce(c,"datarise-511817","asia-south1")
    job=request()
    wrong=BigQueryJobRequest(
        "other-project","asia-south1",job.job_id,job.body,
    )
    with pytest.raises(BigQueryInsertTransportError):
        submit_one_reviewed_job(request=wrong,transport=transport)
    c._call_api.assert_not_called()


def test_real_sdk_client_dispatches_one_rest_insert_without_auth_or_network():
    # Not a live cloud test. Exercise google-cloud-bigquery's actual
    # _call_api binding against a mocked connection transport.
    from google.cloud import bigquery
    from google.auth.credentials import AnonymousCredentials
    c=bigquery.Client(
        project="datarise-511817",credentials=AnonymousCredentials(),
        location="asia-south1",
    )
    job=request()
    with patch.object(c._connection,"api_request",return_value={
        "jobReference":job.body["jobReference"]
    }) as api:
        observation=submit_one_reviewed_job(
            request=job,
            transport=BigQueryJobInsertOnce(
                c,"datarise-511817","asia-south1"),
        )
    assert observation.classification=="INSERT_ACKNOWLEDGED_UNVERIFIED"
    api.assert_called_once()
    assert api.call_args.kwargs["path"]=="/projects/datarise-511817/jobs"
    assert api.call_args.kwargs["data"]==job.body
