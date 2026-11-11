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
