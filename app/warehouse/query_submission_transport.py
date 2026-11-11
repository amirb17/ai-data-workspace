"""414F1 provider submission attempt semantics.

This module has NO real BigQuery SDK binding. An explicit backend transport
is supplied by an internal caller, and the transport must never retry.
The caller must have already committed the durable send boundary and
obtained independent runtime authorization; this adapter is NOT a permit.
"""
from dataclasses import dataclass
from typing import Protocol

from app.warehouse.query_job_request import BigQueryJobRequest


class ExactJobInsertTransport(Protocol):
    def insert_job_once(self, *, project: str, location: str, body: dict) -> object:
        """Make at most ONE jobs.insert attempt without an SDK/network retry."""


@dataclass(frozen=True)
class SubmissionObservation:
    job_id: str
    classification: str
    lookup_required: bool = True
    # Even a successful acknowledgement is not provider metadata attestation.
    execution_authorized: bool = False
    verified: bool = False


def submit_exact_job_attempt_after_boundary(
    *, request: BigQueryJobRequest, transport: ExactJobInsertTransport,
) -> SubmissionObservation:
    """Perform ONE insert and require independent jobs.get regardless of result.

    Use only from a trusted, separately released worker after the durable
    boundary was committed. Never return provider payloads/exception details
    and NEVER retry or create a replacement job identity here.
    """
    if type(request) is not BigQueryJobRequest:
        raise TypeError("Trusted exact BigQuery job request required")
    if (type(request.body) is not dict
            or request.body.get("jobReference") != {
                "projectId": request.project,
                "location": request.location,
                "jobId": request.job_id,
            }):
        raise ValueError("Exact job reference mismatch")
    if not callable(getattr(transport, "insert_job_once", None)):
        raise TypeError("Reviewed insert transport required")
    try:
        transport.insert_job_once(
            project=request.project, location=request.location,
            body=request.body,
        )
    except Exception:
        # Lost response, conflict, forbidden, deadline or an unknown failure
        # all require jobs.get for the SAME pre-pinned job identity.
        return SubmissionObservation(request.job_id, "INSERT_OUTCOME_UNKNOWN")
    return SubmissionObservation(request.job_id, "INSERT_ACKNOWLEDGED_UNVERIFIED")
