"""414F5: read-only warehouse result eligibility from settled PostgreSQL facts.

Does not retrieve data, accept SQL, or issue any authorization credential.
A result fetcher must separately enforce the compiled columns and cursor
ownership, and bind an approved real BigQuery read runtime.
"""
from dataclasses import dataclass

from app.db import warehouse_query_send_repository as repo


class ResultEligibilityConflict(ValueError):
    pass


@dataclass(frozen=True)
class ResultEligibility:
    attempt_id: int
    reservation_id: int
    project: str
    location: str
    job_id: str
    max_page_rows: int
    classification: str = "SETTLED_SUCCESS_RESULTS_REVIEW_REQUIRED"
    provider_dispatched: bool = False
    result_fetched: bool = False


def inspect_settled_result_eligibility(*, attempt_id: int) -> ResultEligibility:
    """Require exact original successful job, verified accounting and pins."""
    if type(attempt_id) is not int or attempt_id < 1:
        raise ValueError("Server attempt ID required")
    with repo.transaction() as conn:
        checkpoint = repo.checkpoint(conn, attempt_id)
        reservation = repo.lock_identity(conn, checkpoint)
        intent, event = repo.latest(conn, attempt_id)
        if reservation["status"] != "COMPLETED":
            raise ResultEligibilityConflict("SETTLED_SUCCESS_REQUIRED")
        if event["phase"] != "SUCCEEDED_OBSERVED" or event["event"] != "OBSERVATION" or event["observation"] != "SUCCEEDED":
            raise ResultEligibilityConflict("VERIFIED_SUCCESS_JOURNAL_REQUIRED")
        if not repo.one(conn, """
            SELECT 1 FROM warehouse_query_send_journal
            WHERE attempt_id=%s AND event='SEND_BOUNDARY'
        """, (attempt_id,)):
            raise ResultEligibilityConflict("ORIGINAL_SEND_BOUNDARY_REQUIRED")
        evidence = repo.one(conn, """
            SELECT a.verdict,a.observation,a.billing_complete,
                   a.bytes_processed,a.bytes_billed,a.payload_sha256,
                   a.policy_sha256,p.expected_sha256,p.expected,
                   e.provider_job_id AS envelope_job,e.policy_sha256 AS envelope_policy
            FROM warehouse_query_recovery_attestations a
            JOIN warehouse_query_provider_policies p USING(attempt_id)
            JOIN warehouse_query_request_envelopes e USING(attempt_id)
            WHERE a.attempt_id=%s AND a.journal_revision=%s
        """, (attempt_id,event["revision"]))
        usage=repo.one(conn,"SELECT * FROM query_governance_usage WHERE reservation_id=%s",(reservation["reservation_id"],))
        if (not evidence or not usage
                or evidence["verdict"] != "VERIFIED_METADATA"
                or evidence["observation"] != "SUCCEEDED"
                or not evidence["billing_complete"]
                or not evidence["payload_sha256"]
                or evidence["policy_sha256"] != evidence["expected_sha256"]
                or evidence["envelope_policy"] != evidence["expected_sha256"]
                or evidence["envelope_job"] != intent["provider_job_id"]
                or intent["provider_job_id"] != checkpoint["provider_job_id"]
                or evidence["expected"].get("job_id") != intent["provider_job_id"]
                or evidence["expected"].get("project") != intent["provider_project"]
                or evidence["expected"].get("location") != intent["provider_location"]
                or usage["provider_job_id"] != intent["provider_job_id"]
                or usage["outcome"] != "SUCCEEDED"
                or usage["accounting_basis"] != "ACTUAL"
                or usage["bytes_processed"] != evidence["bytes_processed"]
                or usage["bytes_billed"] != evidence["bytes_billed"]
                or usage["accounted_bytes"] != max(evidence["bytes_processed"],evidence["bytes_billed"])):
            raise ResultEligibilityConflict("EXACT_SETTLED_JOB_EVIDENCE_REQUIRED")
        requested=reservation["output_rows"]
        if type(requested) is not int or requested < 1:
            raise ResultEligibilityConflict("BOUNDED_OUTPUT_ROWS_REQUIRED")
        return ResultEligibility(
            attempt_id,reservation["reservation_id"],intent["provider_project"],
            intent["provider_location"],intent["provider_job_id"],min(requested,100),
        )
