"""Trusted backend job admission and lease CAS; never executes processing stages.

Caller supplies an authenticated user ID, never an identity from a queue body.
Every operation participates in repository_transaction; inner transactions are
savepoints so failures cannot partially admit or transition a job.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from app.db.database import get_connection

OPERATIONS = frozenset({'BRONZE', 'CONTINUE', 'SILVER', 'GOLD'})
FAILURES = frozenset({'PROCESSING_FAILED', 'WORKER_INTERRUPTED'})


class ClaimOutcome(StrEnum):
    CLAIMED = 'CLAIMED'
    ALREADY_SUCCEEDED = 'ALREADY_SUCCEEDED'
    TERMINAL_FAILED = 'TERMINAL_FAILED'
    BUSY = 'BUSY'
    NOT_DUE = 'NOT_DUE'
    NOT_FOUND = 'NOT_FOUND'
    SOURCE_INELIGIBLE = 'SOURCE_INELIGIBLE'


@dataclass(frozen=True, slots=True)
class JobLease:
    """Internal worker capability; never serialize or log the lease token."""
    job_id: UUID
    lease_token: UUID = field(repr=False)
    lease_expires_at: datetime
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ClaimResult:
    outcome: ClaimOutcome
    lease: JobLease | None = None
    retry_at: datetime | None = None


@dataclass(frozen=True, slots=True, repr=False)
class JobExecutionScope:
    """Trusted internal scope, never supplied by the queue or logged."""
    requested_by: int
    upload_id: int
    workspace_id: int
    dataset_id: int
    file_id: int
    operation: str


class IdempotencyConflict(ValueError):
    """The key already identifies a different immutable request."""


@contextmanager
def _transaction():
    with get_connection() as conn:
        # repository_transaction borrows a connection whose first query may not
        # have started its outer transaction yet. Start it before opening a
        # savepoint, so an inner transaction cannot commit the caller's work.
        if conn.info.transaction_status == TransactionStatus.IDLE:
            conn.execute('BEGIN')
        with conn.transaction():
            yield conn


def _source(conn, user_id, upload_id):
    # Current resource ownership is authoritative, not upload_requests.user_id.
    # SHARE locks keep admission/claim authorization stable until commit.
    return conn.execute("""SELECT u.workspace_id, u.dataset_id, u.file_id
        FROM upload_requests u
        JOIN datasets d ON d.dataset_id=u.dataset_id AND d.workspace_id=u.workspace_id
        JOIN workspaces w ON w.workspace_id=d.workspace_id
        JOIN users actor ON actor.owner_key=w.owner AND actor.owner_key=d.owner
        JOIN physical_files f ON f.file_id=u.file_id
        WHERE u.upload_id=%s AND actor.user_id=%s AND actor.account_status='ACTIVE'
          AND u.status='UPLOADED' AND u.archived_at IS NULL
          AND (u.staging_object_key IS NULL OR
               (u.completed_at IS NOT NULL AND u.completion_result IS NOT NULL))
        FOR SHARE OF u,d,w,actor,f""", (upload_id, user_id)).fetchone()


def create_job(user_id, upload_id, operation, idempotency_key, *, max_attempts=3):
    """Atomically authorize and admit; replay returns the original durable job.

    Key scope is authenticated user + operation, across uploads. Reusing a key
    for another upload or retry budget is an error, including terminal jobs.
    """
    if operation not in OPERATIONS:
        raise ValueError('Unsupported processing operation')
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128 or not idempotency_key.strip():
        raise ValueError('A nonempty idempotency key of at most 128 characters is required')
    if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
        raise ValueError('Invalid retry budget')
    with _transaction() as conn:
        source = _source(conn, user_id, upload_id)
        if source is None:
            raise PermissionError('Upload is unavailable or not eligible for this principal')
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""INSERT INTO processing_jobs
                (requested_by,upload_id,workspace_id,dataset_id,file_id,operation,idempotency_key,max_attempts)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (requested_by,operation,idempotency_key) DO NOTHING RETURNING *""",
                (user_id, upload_id, *source, operation, idempotency_key, max_attempts))
            job = cur.fetchone()
            if job is None:
                cur.execute("""SELECT * FROM processing_jobs
                    WHERE requested_by=%s AND operation=%s AND idempotency_key=%s""",
                    (user_id, operation, idempotency_key))
                job = cur.fetchone()
            if (job['upload_id'], job['workspace_id'], job['dataset_id'], job['file_id'], job['max_attempts']) != (upload_id, *source, max_attempts):
                raise IdempotencyConflict('Idempotency key identifies a different request')
            return job


def claim_job(job_id, *, lease_seconds=120):
    """Compatibility API: full claimed row, or None for every other outcome."""
    return _claim_job(job_id, lease_seconds=lease_seconds)[1]


def claim_job_result(job_id, *, lease_seconds=120) -> ClaimResult:
    """Atomically resolve and optionally claim a persisted job for a worker.

    BUSY is conservative for a locked row; retry_at is known only for a visible
    active lease or delayed job. Exceptions propagate and must not be ACKed.
    Results inside repository_transaction are provisional until its commit.
    Tokens fence job metadata, not processing side effects.
    """
    return _claim_job(job_id, lease_seconds=lease_seconds)[0]


def _claim_job(job_id, *, lease_seconds):
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 900:
        raise ValueError('Lease must be between 1 and 900 seconds')
    with _transaction() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM processing_jobs WHERE job_id=%s FOR UPDATE SKIP LOCKED', (job_id,))
        job = cur.fetchone()
        if job is None:
            # SKIP LOCKED alone cannot distinguish a missing row from contention.
            # MVCC existence lookup does not wait for a competing row lock.
            exists = conn.execute('SELECT EXISTS(SELECT 1 FROM processing_jobs WHERE job_id=%s)',
                                  (job_id,)).fetchone()[0]
            return ClaimResult(ClaimOutcome.BUSY if exists else ClaimOutcome.NOT_FOUND), None
        if job['status'] == 'SUCCEEDED':
            return ClaimResult(ClaimOutcome.ALREADY_SUCCEEDED), None
        if job['status'] in {'FAILED', 'CANCELLED'}:
            outcome = (ClaimOutcome.SOURCE_INELIGIBLE if job['failure_code'] == 'SOURCE_INELIGIBLE'
                       else ClaimOutcome.TERMINAL_FAILED)
            return ClaimResult(outcome), None
        now = conn.execute('SELECT clock_timestamp()').fetchone()[0]
        expired = job['status'] == 'RUNNING'
        if expired and job['lease_expires_at'] > now:
            return ClaimResult(ClaimOutcome.BUSY, retry_at=job['lease_expires_at']), None
        if not expired and job['available_at'] > now:
            return ClaimResult(ClaimOutcome.NOT_DUE, retry_at=job['available_at']), None
        source = _source(conn, job['requested_by'], job['upload_id'])
        if source != (job['workspace_id'], job['dataset_id'], job['file_id']):
            code = 'SOURCE_INELIGIBLE'
        elif job['attempt_count'] >= job['max_attempts']:
            code = 'LEASE_EXPIRED'
        else:
            cur.execute("""UPDATE processing_jobs SET status='RUNNING',
                attempt_count=attempt_count+1, recovery_count=recovery_count+%s,
                lease_token=gen_random_uuid(), lease_expires_at=clock_timestamp()+%s*interval '1 second',
                last_claimed_at=clock_timestamp(), updated_at=clock_timestamp(),
                failure_code=CASE WHEN %s THEN 'LEASE_EXPIRED' ELSE failure_code END
                WHERE job_id=%s RETURNING *""", (int(expired), lease_seconds, expired, job_id))
            claimed = cur.fetchone()
            lease = JobLease(claimed['job_id'], claimed['lease_token'],
                             claimed['lease_expires_at'], claimed['attempt_count'])
            return ClaimResult(ClaimOutcome.CLAIMED, lease=lease), claimed
        cur.execute("""UPDATE processing_jobs SET status='FAILED',failure_code=%s,
            lease_token=NULL,lease_expires_at=NULL,completed_at=clock_timestamp(),
            updated_at=clock_timestamp() WHERE job_id=%s""", (code, job_id))
        outcome = (ClaimOutcome.SOURCE_INELIGIBLE if code == 'SOURCE_INELIGIBLE'
                   else ClaimOutcome.TERMINAL_FAILED)
        return ClaimResult(outcome), None


def renew_job_lease(job_id, lease_token, *, lease_seconds=120) -> JobLease | None:
    """Extend a live matching lease from PostgreSQL time, without rotating it.

    Never resurrect an expired lease or shorten a concurrent longer renewal.
    Like other repository operations, renewal is provisional in an outer tx.
    """
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 900:
        raise ValueError('Lease must be between 1 and 900 seconds')
    with _transaction() as conn:
        conn.execute('SELECT job_id FROM processing_jobs WHERE job_id=%s FOR UPDATE', (job_id,))
        row = conn.execute("""UPDATE processing_jobs SET
            lease_expires_at=GREATEST(lease_expires_at,clock_timestamp()+%s*interval '1 second'),
            updated_at=clock_timestamp()
            WHERE job_id=%s AND status='RUNNING' AND lease_token=%s
              AND lease_expires_at>clock_timestamp()
            RETURNING job_id,lease_token,lease_expires_at,attempt_count""",
            (lease_seconds, job_id, lease_token)).fetchone()
        return JobLease(*row) if row else None


def get_execution_scope(job_id, lease_token, *, minimum_lease_ms=0) -> JobExecutionScope | None:
    """Revalidate token, source and current ownership; admit no work on failure.

    This is a short transaction, not authorization held across external I/O.
    Recheck the DB clock after acquiring ownership/source locks as well.
    """
    if type(minimum_lease_ms) is not int or not 0 <= minimum_lease_ms <= 900000:
        raise ValueError('Invalid minimum lease duration')
    with _transaction() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT * FROM processing_jobs WHERE job_id=%s AND status='RUNNING'
            AND lease_token=%s FOR UPDATE""", (job_id, lease_token))
        job = cur.fetchone()
        if job is None:
            return None
        source = _source(conn, job['requested_by'], job['upload_id'])
        if source != (job['workspace_id'], job['dataset_id'], job['file_id']):
            return None
        enough = conn.execute("""SELECT lease_expires_at>clock_timestamp()+%s*interval '1 millisecond'
            FROM processing_jobs WHERE job_id=%s""", (minimum_lease_ms, job_id)).fetchone()[0]
        if not enough:
            return None
        return JobExecutionScope(*(job[k] for k in ('requested_by', 'upload_id', 'workspace_id',
                                                   'dataset_id', 'file_id', 'operation')))


def finish_job(job_id, lease_token, *, failure_code=None, retry=False, retry_delay_seconds=0,
               result_code='COMPLETED'):
    """Fenced completion/failure. Persist only allowlisted, non-sensitive metadata."""
    if failure_code is not None and failure_code not in FAILURES:
        raise ValueError('Unsupported safe failure code')
    if result_code not in {'COMPLETED', 'SUCCESS_WITH_WARNINGS'}:
        raise ValueError('Unsupported safe result code')
    if retry and failure_code is None:
        raise ValueError('Only a failed execution can be retried')
    if type(retry_delay_seconds) is not int or not 0 <= retry_delay_seconds <= 86400:
        raise ValueError('Invalid retry delay')
    with _transaction() as conn, conn.cursor(row_factory=dict_row) as cur:
        # Evaluate lease validity after acquiring the row lock, not before a wait.
        cur.execute('SELECT job_id FROM processing_jobs WHERE job_id=%s FOR UPDATE', (job_id,))
        cur.execute("""UPDATE processing_jobs SET
            status=CASE WHEN %s::text IS NULL THEN 'SUCCEEDED'
                        WHEN %s AND attempt_count<max_attempts THEN 'RETRY_WAIT' ELSE 'FAILED' END,
            result_code=%s, failure_code=%s,
            available_at=clock_timestamp()+%s*interval '1 second',
            completed_at=CASE WHEN %s::text IS NOT NULL AND %s AND attempt_count<max_attempts
                              THEN NULL ELSE clock_timestamp() END,
            lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
            WHERE job_id=%s AND status='RUNNING' AND lease_token=%s
                AND lease_expires_at>clock_timestamp() RETURNING *""",
            (failure_code, retry, result_code if failure_code is None else None, failure_code,
             retry_delay_seconds, failure_code, retry, job_id, lease_token))
        return cur.fetchone()
