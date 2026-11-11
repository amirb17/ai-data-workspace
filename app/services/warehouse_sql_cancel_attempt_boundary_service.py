"""414F4 one-way cancellation attempt fence. No cloud IO or permit."""
from dataclasses import dataclass
from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_send_recovery_service as recovery

@dataclass(frozen=True)
class CancelBoundary:
    attempt_id: int
    job_id: str
    classification: str = "CANCEL_POSSIBLY_SENT_LOOKUP_REQUIRED"
    cancellation_confirmed: bool = False
    settlement_authorized: bool = False
    provider_dispatched: bool = False

def record_cancel_attempt_boundary_without_provider(
    *, lease: recovery.RecoveryLease, expected_revision: int,
) -> CancelBoundary:
    """Commit once before possible cancel; subsequent workers use jobs.get."""
    recovery._validate_lease(lease)
    with recovery._worker(lease.attempt_id) as (conn, checkpoint):
        reservation = repo.lock_identity(conn, checkpoint)
        intent, event = repo.latest(conn, lease.attempt_id)
        repo.require_owner(conn, event, lease, expected_revision)
        if reservation["status"] != "EXECUTING":
            raise repo.RecoveryConflict("CANCEL_BOUNDARY_RESERVATION_SETTLED")
        if event["phase"] not in ("SEND_UNKNOWN", "PROVIDER_RUNNING"):
            raise repo.RecoveryConflict("CANCEL_BOUNDARY_REQUIRES_POSSIBLE_SEND")
        pinned = repo.one(conn, """
            SELECT provider_job_id,request_sha256
            FROM warehouse_query_request_envelopes WHERE attempt_id=%s
        """, (lease.attempt_id,))
        requested = repo.one(conn, """
            SELECT provider_job_id,request_sha256
            FROM warehouse_query_cancel_intents WHERE attempt_id=%s
        """, (lease.attempt_id,))
        if (not pinned or not requested
                or pinned["provider_job_id"] != intent["provider_job_id"]
                or requested != pinned
                or lease.job != recovery._identity(intent)):
            raise repo.RecoveryConflict("EXACT_CANCEL_JOB_AND_INTENT_REQUIRED")
        if repo.one(conn, """
            SELECT 1 FROM warehouse_query_cancel_attempt_boundaries
            WHERE attempt_id=%s
        """, (lease.attempt_id,)):
            raise repo.RecoveryConflict("CANCEL_ALREADY_POSSIBLY_SENT_LOOKUP_ONLY")
        conn.execute("""
            INSERT INTO warehouse_query_cancel_attempt_boundaries
               (attempt_id,provider_job_id,request_sha256,journal_revision)
            VALUES (%s,%s,%s,%s)
        """, (lease.attempt_id,intent["provider_job_id"],
              pinned["request_sha256"],event["revision"]))
        return CancelBoundary(lease.attempt_id,intent["provider_job_id"])


def lookup_after_cancel_boundary_without_resend(
    *, lease: recovery.RecoveryLease, expected_revision: int,
):
    """Use the original BigQuery jobs.get attestation after possible cancel.

    Must prove the committed cancellation-attempt boundary and pin first.
    A retry or takeover has no cancel transport here. This method does not
    authorize settlement or claim that cancellation succeeded.
    """
    from app.services import warehouse_sql_provider_policy_service as policy
    recovery._validate_lease(lease)
    with recovery._worker(lease.attempt_id) as (conn, checkpoint):
        reservation = repo.lock_identity(conn, checkpoint)
        intent, event = repo.latest(conn, lease.attempt_id)
        repo.require_owner(conn, event, lease, expected_revision)
        if reservation["status"] != "EXECUTING":
            raise repo.RecoveryConflict("CANCEL_RECOVERY_SETTLED_INSPECT_ONLY")
        if event["phase"] not in ("SEND_UNKNOWN", "PROVIDER_RUNNING"):
            raise repo.RecoveryConflict("CANCEL_RECOVERY_REQUIRES_RUNNING_OR_UNKNOWN_JOB")
        pinned = repo.one(conn, """
            SELECT provider_job_id,request_sha256,policy_sha256
            FROM warehouse_query_request_envelopes WHERE attempt_id=%s
        """, (lease.attempt_id,))
        requested = repo.one(conn, """
            SELECT provider_job_id,request_sha256
            FROM warehouse_query_cancel_intents WHERE attempt_id=%s
        """, (lease.attempt_id,))
        marker = repo.one(conn, """
            SELECT provider_job_id,request_sha256,journal_revision
            FROM warehouse_query_cancel_attempt_boundaries WHERE attempt_id=%s
        """, (lease.attempt_id,))
        if (not pinned or not requested or not marker
                or pinned["provider_job_id"] != intent["provider_job_id"]
                or pinned["provider_job_id"] != checkpoint["provider_job_id"]
                or requested["provider_job_id"] != pinned["provider_job_id"]
                or marker["provider_job_id"] != pinned["provider_job_id"]
                or requested["request_sha256"] != pinned["request_sha256"]
                or marker["request_sha256"] != pinned["request_sha256"]
                or lease.job != recovery._identity(intent)):
            raise repo.RecoveryConflict("IMMUTABLE_CANCEL_BOUNDARY_AND_JOB_REQUIRED")
    # The ordinary attestation path independently rechecks CAS and policy
    # before and after a GET. No cancellation call or permission is exposed.
    return policy.lookup_pinned_submission_without_resend(
        lease=lease, expected_revision=expected_revision,
    )
