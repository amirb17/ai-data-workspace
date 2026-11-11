"""414F1 simulated exact one-attempt provider transport (no GCP)."""
from copy import deepcopy

import pytest

from app.warehouse.query_job_request import (
    BigQueryJobRequest, build_reviewed_query_job_request,
)
from app.warehouse.query_submission_transport import (
    SubmissionObservation, submit_exact_job_attempt_after_boundary,
)
from tests.test_warehouse_query_job_request_414f1 import SQL, policy


class FakeInsert:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def insert_job_once(self, *, project, location, body):
        self.calls.append((project, location, deepcopy(body)))
        if self.failure:
            raise self.failure
        return {"jobReference": body["jobReference"]}


def prepared():
    return build_reviewed_query_job_request(expected=policy(), compiled_sql=SQL)


def test_success_ack_is_not_verification_or_settlement():
    job, transport = prepared(), FakeInsert()
    result = submit_exact_job_attempt_after_boundary(request=job, transport=transport)
    assert result == SubmissionObservation(job.job_id, "INSERT_ACKNOWLEDGED_UNVERIFIED")
    assert result.lookup_required and not result.verified and not result.execution_authorized
    assert transport.calls == [(job.project, job.location, job.body)]


@pytest.mark.parametrize("failure", [
    TimeoutError("provider detail must not escape"),
    ConnectionError("network connection lost"),
    RuntimeError("duplicate job conflict"),
    PermissionError("not permitted"),
])
def test_any_insert_error_is_unknown_and_never_retried(failure):
    job, transport = prepared(), FakeInsert(failure)
    result = submit_exact_job_attempt_after_boundary(request=job, transport=transport)
    assert result.classification == "INSERT_OUTCOME_UNKNOWN"
    assert result.job_id == job.job_id and result.lookup_required
    assert len(transport.calls) == 1
    assert "provider detail" not in str(result)


def test_rejects_untyped_or_forged_job_reference_before_transport():
    transport = FakeInsert()
    job = prepared()
    with pytest.raises(TypeError):
        submit_exact_job_attempt_after_boundary(request=job.body, transport=transport)
    forged = BigQueryJobRequest(
        job.project, job.location, job.job_id,
        {**job.body, "jobReference": {**job.body["jobReference"], "jobId": "foreign"}},
    )
    with pytest.raises(ValueError, match="reference"):
        submit_exact_job_attempt_after_boundary(request=forged, transport=transport)
    assert transport.calls == []


def test_no_ambient_sdk_or_transport_defaults():
    with pytest.raises(TypeError):
        submit_exact_job_attempt_after_boundary(request=prepared(), transport=object())
