"""Transactional artifact publication.

A publication operation must:
- validate the active job lease under PostgreSQL row lock;
- validate the candidate against its pinned source;
- commit the publication receipt and authoritative head atomically.

No S3 access or stage execution belongs in this module.
"""

from contextlib import contextmanager
from app.processing_worker.artifact_contract import VerifiedCandidate
from app.db import database
from uuid import UUID

from psycopg.rows import dict_row
class ArtifactPublicationError(Exception):
    """Publication cannot proceed safely."""


class StaleWorkerError(ArtifactPublicationError):
    """The job lease is expired or no longer belongs to this worker."""


class InvalidCandidateError(ArtifactPublicationError):
    """The candidate is not eligible for publication."""
@contextmanager
def _transaction():
    """Use a local transaction or participate in an existing outer transaction."""
    with database.get_connection() as conn:
        with conn.transaction():
            yield conn
def _require_active_job_lease(conn, job_id: UUID, lease_token: UUID):
    """Lock job and verify the lease against PostgreSQL's current clock."""
    if not isinstance(job_id, UUID) or not isinstance(lease_token, UUID):
        raise ValueError("Job ID and lease token must be UUIDs")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT *
            FROM processing_jobs
            WHERE job_id = %s
            FOR UPDATE
            """,
            (job_id,),
        )
        job = cur.fetchone()

    if job is None:
        raise StaleWorkerError("Processing job does not exist")

    if (
        job["status"] != "RUNNING"
        or job["lease_token"] != lease_token
    ):
        raise StaleWorkerError("Worker no longer owns this job")

    # Check the time AFTER acquiring the row lock.
    still_valid = conn.execute(
        """
        SELECT lease_expires_at > clock_timestamp()
        FROM processing_jobs
        WHERE job_id = %s
        """,
        (job_id,),
    ).fetchone()[0]

    if not still_valid:
        raise StaleWorkerError("Processing job lease expired")

    return job
def _require_current_job_source(conn, job):
    """Revalidate the original job's current authorization and source pins.

    Uses the same eligibility policy as processing-job admission and claiming.
    """
    from app.db.processing_job_repository import _source

    source = _source(
        conn,
        job["requested_by"],
        job["upload_id"],
    )

    expected = (
        job["workspace_id"],
        job["dataset_id"],
        job["file_id"],
    )

    if source != expected:
        raise ArtifactPublicationError(
            "Job source or current ownership is no longer eligible"
        )
def _require_verified_candidate(conn, candidate_id, job_id, lease_token):
    """Lock and validate the candidate and its originating stage intent."""
    if not isinstance(candidate_id, UUID):
        raise ValueError("Candidate ID must be a UUID")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                c.candidate_id,
                c.intent_id,
                c.manifest_sha256,
                c.status,
                c.verified_at,
                i.job_id,
                i.lease_token,
                i.stage,
                i.file_id,
                i.dataset_version_file_id,
                i.input_fingerprint
            FROM processing_artifact_candidates AS c
            JOIN processing_stage_intents AS i
                ON i.intent_id = c.intent_id
            WHERE c.candidate_id = %s
            FOR UPDATE OF c, i
            """,
            (candidate_id,),
        )
        candidate = cur.fetchone()

    if candidate is None:
        raise InvalidCandidateError("Candidate does not exist")

    if candidate["job_id"] != job_id or candidate["lease_token"] != lease_token:
        raise InvalidCandidateError("Candidate belongs to a different worker attempt")

    if candidate["status"] != "VERIFIED" or candidate["verified_at"] is None:
        raise InvalidCandidateError("Candidate has not been verified")

    return candidate
def _get_existing_receipt(conn, candidate_id):
    """Return an existing publication receipt for a candidate, if any."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT receipt_id, intent_id, candidate_id,
                   job_id, lease_token, published_at
            FROM processing_stage_receipts
            WHERE candidate_id = %s
            FOR UPDATE
            """,
            (candidate_id,),
        )
        return cur.fetchone()
def publish_candidate(job_id: UUID, lease_token: UUID, candidate_id: UUID):
    """Publish a verified candidate with a fenced PostgreSQL transaction.

    Does not perform S3 verification or execute processing stages.
    """
    with _transaction() as conn:
        # Always lock the job first to maintain a consistent lock order.
        job = _require_active_job_lease(conn, job_id, lease_token)
        _require_current_job_source(conn, job)

        candidate = _require_verified_candidate(
            conn, candidate_id, job_id, lease_token
        )

        # The candidate must originate from the job's physical file.
        if candidate["file_id"] != job["file_id"]:
            raise InvalidCandidateError("Candidate source file does not match job")

        # A Bronze job must not publish Silver or Gold.
        allowed_operations = {
            "BRONZE": {"BRONZE"},
            "SILVER": {"SILVER"},
            "GOLD": {"GOLD"},
            "CONTINUE": {"SILVER", "GOLD"},
        }

        if candidate["stage"] not in allowed_operations.get(
            job["operation"], set()
        ):
            raise InvalidCandidateError("Stage is not authorized by job operation")

        # Validate logical dataset scope for Silver and Gold.
        if candidate["stage"] in {"SILVER", "GOLD"}:
            source = conn.execute(
                """
                SELECT dv.dataset_id
                FROM dataset_version_files AS dvf
                JOIN dataset_versions AS dv
                    ON dv.dataset_version_id = dvf.dataset_version_id
                WHERE dvf.dataset_version_file_id = %s
                  AND dvf.file_id = %s
                """,
                (
                    candidate["dataset_version_file_id"],
                    candidate["file_id"],
                ),
            ).fetchone()

            if source is None or source[0] != job["dataset_id"]:
                raise InvalidCandidateError("Candidate dataset scope mismatch")

        existing = _get_existing_receipt(conn, candidate_id)

        if existing is not None:
            return existing

        receipt = conn.execute(
            """
            INSERT INTO processing_stage_receipts (
                intent_id,
                candidate_id,
                job_id,
                lease_token,
                stage,
                file_id,
                dataset_version_file_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING
                receipt_id,
                intent_id,
                candidate_id,
                job_id,
                lease_token,
                published_at
            """,
            (
                candidate["intent_id"],
                candidate_id,
                job_id,
                lease_token,
                candidate["stage"],
                candidate["file_id"],
                candidate["dataset_version_file_id"],
            ),
        ).fetchone()

        # Head and receipt must be committed together.
        head_result = conn.execute(
        """
        INSERT INTO processing_stage_heads
            (stage, file_id, dataset_version_file_id,
            receipt_id, candidate_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT ON CONSTRAINT uq_stage_head_scope
        DO NOTHING
        RETURNING candidate_id
        """,
        (
            candidate["stage"],
            candidate["file_id"],
            candidate["dataset_version_file_id"],
            receipt[0],
            candidate_id,
        ),
        ).fetchone()

        if head_result is None:
            raise ArtifactPublicationError(
            "Another candidate is already authoritative for this stage scope"
        )
        return {
            "receipt_id": receipt[0],
            "candidate_id": candidate_id,
            "stage": candidate["stage"],
        }

def mark_candidate_verified(
    job_id: UUID,
    lease_token: UUID,
    evidence: VerifiedCandidate,
):
    """Persist verified artifact evidence under an active job lease.

    The caller must be the trusted verification service.
    This function does not read S3 or validate Parquet semantics.
    """
    if not isinstance(evidence, VerifiedCandidate):
        raise ValueError("Expected VerifiedCandidate evidence")

    with _transaction() as conn:
        job = _require_active_job_lease(
            conn, job_id, lease_token
        )
        _require_current_job_source(conn, job)

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    c.candidate_id,
                    c.intent_id,
                    c.manifest_sha256,
                    c.status,
                    c.verified_at,
                    i.job_id,
                    i.lease_token,
                    i.stage,
                    i.file_id,
                    i.input_fingerprint
                FROM processing_artifact_candidates AS c
                JOIN processing_stage_intents AS i
                    ON i.intent_id = c.intent_id
                WHERE c.candidate_id = %s
                FOR UPDATE OF c, i
                """,
                (evidence.candidate_id,),
            )
            row = cur.fetchone()

        if row is None:
            raise InvalidCandidateError("Candidate not found")

        if (
            row["job_id"] != job_id
            or row["lease_token"] != lease_token
            or row["file_id"] != job["file_id"]
            or row["intent_id"] != evidence.intent_id
            or row["stage"] != evidence.stage
            or row["input_fingerprint"] != evidence.input_fingerprint
            or row["manifest_sha256"] != evidence.manifest_sha256
        ):
            raise InvalidCandidateError(
                "Verification evidence does not match registered candidate"
            )

        if row["status"] != "STAGED":
            raise InvalidCandidateError(
                "Candidate is not awaiting verification"
            )

        updated = conn.execute(
            """
            UPDATE processing_artifact_candidates
            SET status = 'VERIFIED',
                verified_at = clock_timestamp()
            WHERE candidate_id = %s
              AND status = 'STAGED'
            RETURNING candidate_id
            """,
            (evidence.candidate_id,),
        ).fetchone()

        if updated is None:
            raise InvalidCandidateError(
                "Candidate verification transition failed"
            )

        return updated[0]
