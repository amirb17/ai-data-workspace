"""Real BigQuery jobs.insert transport, with exactly one API call per invocation.

No implicit ADC, IAM grant, query construction or execution authorization is
performed here. The independent provider worker must inject an explicitly
constructed BigQuery Client using its own restricted service-account identity.

The durable SEND_BOUNDARY must be committed BEFORE this transport is invoked.
Any response ambiguity, including 409, is handled by independent jobs.get
with the original pinned job ID, never by a new job or an insert retry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.warehouse.query_job_request import BigQueryJobRequest


class BigQueryInsertTransportError(ValueError):
    pass


@dataclass(frozen=True)
class BigQueryJobInsertOnce:
    """Adapter to google-cloud-bigquery Client's underlying jobs.insert API.

    The public Client.query() API can synthesize/query-retry jobs. It is not
    suitable for the exact-once-attempt boundary. _call_api is deliberately
    isolated here behind a small testable adapter; pin google-cloud-bigquery
    in deployment and include its internal call contract in regression tests.
    """

    client: Any = field(repr=False)
    project: str
    location: str

    def __post_init__(self):
        if (type(self.project) is not str or not self.project
                or self.location != "asia-south1"
                or getattr(self.client, "project", None) != self.project
                or not callable(getattr(self.client, "_call_api", None))):
            raise BigQueryInsertTransportError(
                "EXPLICIT_SCOPED_BIGQUERY_SUBMISSION_CLIENT_REQUIRED"
            )

    def insert_job_once(self, *, project: str, location: str, body: dict) -> object:
        if (project != self.project or location != self.location
                or type(body) is not dict
                or set(body) != {"jobReference", "configuration"}
                or body.get("jobReference", {}).get("projectId") != project
                or body["jobReference"].get("location") != location
                or type(body["jobReference"].get("jobId")) is not str
                or body["configuration"].get("jobType") != "QUERY"
                or body["configuration"].get("dryRun") is not False
                or type(body["configuration"].get("query")) is not dict
                or body["configuration"]["query"].get("useLegacySql") is not False
                or not body["configuration"]["query"].get("query")):
            raise BigQueryInsertTransportError("PINNED_BIGQUERY_REQUEST_REQUIRED")

        # REST jobs.insert, not jobs.query. No automatic API retry, no
        # job-level retry, no generated second ID, no provider-side SQL edits.
        return self.client._call_api(
            retry=None,
            timeout=10,
            method="POST",
            path=f"/projects/{project}/jobs",
            data=body,
        )


def submit_one_reviewed_job(
    *, request: BigQueryJobRequest, transport: BigQueryJobInsertOnce,
):
    """Wrap a single cloud attempt; uncertainty always returns LOOKUP_REQUIRED.

    This is NOT the authorization entry point. Only an independently
    authorized process after a committed send fence may call it.
    """
    if (type(request) is not BigQueryJobRequest
            or type(transport) is not BigQueryJobInsertOnce
            or (request.project,request.location)!=(transport.project,transport.location)):
        raise BigQueryInsertTransportError("REVIEWED_CLOUD_JOB_SCOPE_REQUIRED")
    from app.warehouse.query_submission_transport import (
        submit_exact_job_attempt_after_boundary,
    )
    return submit_exact_job_attempt_after_boundary(
        request=request,transport=transport,
    )
