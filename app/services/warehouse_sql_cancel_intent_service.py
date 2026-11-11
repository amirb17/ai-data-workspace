"""414F4: durable cancel request for the original job; no provider IO.

Worker must have a current recovery lease. Cancellation is a request, NOT
evidence of stopped execution or permission to settle/release a reservation.
"""
from dataclasses import dataclass

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_send_recovery_service as recovery


@dataclass(frozen=True)
class CancelIntent:
    attempt_id: int
    job_id: str
    classification: str = 'CANCEL_REQUESTED_LOOKUP_REQUIRED'
    cancellation_confirmed: bool = False
    settlement_authorized: bool = False
    provider_dispatched: bool = False


def record_cancel_intent_without_provider(
    *, lease: recovery.RecoveryLease, expected_revision: int,
) -> CancelIntent:
    """Idempotently persist cancel intent only for exact pinned, possibly sent job."""
    recovery._validate_lease(lease)
    with recovery._worker(lease.attempt_id) as (conn, c):
        reservation = repo.lock_identity(conn, c)
        intent, event = repo.latest(conn, lease.attempt_id)
        repo.require_owner(conn, event, lease, expected_revision)
        if reservation['status'] != 'EXECUTING':
            raise repo.RecoveryConflict('SETTLED_JOB_CANNOT_REQUEST_CANCEL')
        if event['phase'] not in ('SEND_UNKNOWN', 'PROVIDER_RUNNING'):
            raise repo.RecoveryConflict('CANCEL_REQUIRES_POSSIBLE_SEND')
        pin = repo.one(conn, """
            SELECT provider_job_id,request_sha256
            FROM warehouse_query_request_envelopes WHERE attempt_id=%s
        """, (lease.attempt_id,))
        if not pin or pin['provider_job_id'] != intent['provider_job_id']:
            raise repo.RecoveryConflict('PINNED_JOB_REQUIRED_FOR_CANCEL')
        existing = repo.one(conn, """
            SELECT provider_job_id,request_sha256
            FROM warehouse_query_cancel_intents WHERE attempt_id=%s
        """, (lease.attempt_id,))
        if existing:
            if (existing['provider_job_id'] != pin['provider_job_id']
                    or existing['request_sha256'] != pin['request_sha256']):
                raise repo.RecoveryConflict('IMMUTABLE_CANCEL_IDENTITY_DRIFT')
        else:
            conn.execute("""
                INSERT INTO warehouse_query_cancel_intents
                    (attempt_id,provider_job_id,request_sha256,journal_revision)
                VALUES(%s,%s,%s,%s)
            """, (lease.attempt_id, intent['provider_job_id'],
                  pin['request_sha256'], event['revision']))
        return CancelIntent(lease.attempt_id, intent['provider_job_id'])
