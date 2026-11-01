"""Isolated PostgreSQL tests for artifact publication; no AWS access."""

from uuid import uuid4
from app.processing_worker.artifact_contract import VerifiedCandidate
import psycopg
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
from app.db import processing_job_repository as jobs
from app.db import artifact_publication_repository as publication
from test_processing_jobs_postgres import source
from test_rule_approval_postgres import pg
import pytest
def test_verified_bronze_candidate_can_publish(source):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id,
        upload_id,
        "BRONZE",
        "publication-test-1",
    )
    claimed = jobs.claim_job(job["job_id"])
    assert claimed is not None

    intent_id = uuid4()
    candidate_id = uuid4()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_intents
                (intent_id, job_id, lease_token, stage,
                 file_id, input_fingerprint)
            VALUES (%s, %s, %s, 'BRONZE', %s, %s)
            """,
            (
                intent_id,
                job["job_id"],
                claimed["lease_token"],
                job["file_id"],
                "a" * 64,
            ),
        )

        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256,
                 status, verified_at)
            VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
            """,
            (candidate_id, intent_id, "b" * 64),
        )

    receipt = publication.publish_candidate(
        job["job_id"],
        claimed["lease_token"],
        candidate_id,
    )

    assert receipt["candidate_id"] == candidate_id

    with connect() as conn:
        head = conn.execute(
            """
            SELECT candidate_id
            FROM processing_stage_heads
            WHERE stage = 'BRONZE'
              AND file_id = %s
              AND dataset_version_file_id IS NULL
            """,
            (job["file_id"],),
        ).fetchone()

    assert head is not None
    assert head[0] == candidate_id

def test_expired_worker_cannot_publish(source):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id,
        upload_id,
        "BRONZE",
        "publication-expired-lease",
    )
    claimed = jobs.claim_job(job["job_id"])

    intent_id = uuid4()
    candidate_id = uuid4()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_intents
                (intent_id, job_id, lease_token, stage,
                 file_id, input_fingerprint)
            VALUES (%s, %s, %s, 'BRONZE', %s, %s)
            """,
            (
                intent_id,
                job["job_id"],
                claimed["lease_token"],
                job["file_id"],
                "a" * 64,
            ),
        )

        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256,
                 status, verified_at)
            VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
            """,
            (candidate_id, intent_id, "b" * 64),
        )

        # Expire this worker's lease using the database clock.
        conn.execute(
            """
            UPDATE processing_jobs
            SET lease_expires_at =
                clock_timestamp() - interval '1 second'
            WHERE job_id = %s
            """,
            (job["job_id"],),
        )

    with pytest.raises(publication.StaleWorkerError):
        publication.publish_candidate(
            job["job_id"],
            claimed["lease_token"],
            candidate_id,
        )

    # A rejected publication must leave no receipt or head.
    with connect() as conn:
        receipts = conn.execute(
            "SELECT count(*) FROM processing_stage_receipts"
        ).fetchone()[0]

        heads = conn.execute(
            "SELECT count(*) FROM processing_stage_heads"
        ).fetchone()[0]

    assert receipts == 0
    assert heads == 0
def test_reclaimed_job_fences_previous_worker(source):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id, upload_id, "BRONZE", "publication-takeover"
    )
    first = jobs.claim_job(job["job_id"])
    assert first is not None

    old_intent = uuid4()
    old_candidate = uuid4()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_intents
                (intent_id, job_id, lease_token, stage,
                 file_id, input_fingerprint)
            VALUES (%s, %s, %s, 'BRONZE', %s, %s)
            """,
            (
                old_intent, job["job_id"], first["lease_token"],
                job["file_id"], "a" * 64,
            ),
        )
        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256,
                 status, verified_at)
            VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
            """,
            (old_candidate, old_intent, "b" * 64),
        )

        conn.execute(
            """
            UPDATE processing_jobs
            SET lease_expires_at = clock_timestamp() - interval '1 second'
            WHERE job_id = %s
            """,
            (job["job_id"],),
        )

    second = jobs.claim_job(job["job_id"])
    assert second is not None
    assert second["lease_token"] != first["lease_token"]

    with pytest.raises(publication.StaleWorkerError):
        publication.publish_candidate(
            job["job_id"], first["lease_token"], old_candidate
        )

    new_intent = uuid4()
    new_candidate = uuid4()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_intents
                (intent_id, job_id, lease_token, stage,
                 file_id, input_fingerprint)
            VALUES (%s, %s, %s, 'BRONZE', %s, %s)
            """,
            (
                new_intent, job["job_id"], second["lease_token"],
                job["file_id"], "a" * 64,
            ),
        )
        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256,
                 status, verified_at)
            VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
            """,
            (new_candidate, new_intent, "c" * 64),
        )

    result = publication.publish_candidate(
        job["job_id"], second["lease_token"], new_candidate
    )
    assert result["candidate_id"] == new_candidate

    with connect() as conn:
        head = conn.execute(
            """
            SELECT candidate_id
            FROM processing_stage_heads
            WHERE stage = 'BRONZE' AND file_id = %s
            """,
            (job["file_id"],),
        ).fetchone()
        assert head[0] == new_candidate

        assert conn.execute(
            "SELECT COUNT(*) FROM processing_stage_receipts"
        ).fetchone()[0] == 1
def test_publication_failure_rolls_back_receipt_and_head(source):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id, upload_id, "BRONZE", "publication-rollback"
    )
    claimed = jobs.claim_job(job["job_id"])
    assert claimed is not None

    intent_id = uuid4()
    candidate_id = uuid4()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_intents
                (intent_id, job_id, lease_token, stage,
                 file_id, input_fingerprint)
            VALUES (%s, %s, %s, 'BRONZE', %s, %s)
            """,
            (
                intent_id, job["job_id"], claimed["lease_token"],
                job["file_id"], "a" * 64,
            ),
        )
        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256,
                 status, verified_at)
            VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
            """,
            (candidate_id, intent_id, "b" * 64),
        )

    # Force the head update to fail after receipt insertion.
    with connect() as conn:
        conn.execute(
            """
            CREATE FUNCTION reject_test_head()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'simulated head failure';
            END;
            $$
            """
        )
        conn.execute(
            """
            CREATE TRIGGER reject_test_head_insert
            BEFORE INSERT ON processing_stage_heads
            FOR EACH ROW EXECUTE FUNCTION reject_test_head()
            """
        )

    with pytest.raises(psycopg.errors.RaiseException, match="simulated head failure"):
        publication.publish_candidate(
            job["job_id"], claimed["lease_token"], candidate_id
        )

    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM processing_stage_receipts"
        ).fetchone()[0] == 0

        assert conn.execute(
            "SELECT COUNT(*) FROM processing_stage_heads"
        ).fetchone()[0] == 0

def test_repeated_publication_preserves_receipt_and_head(source):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id, upload_id, "BRONZE", "publication-idempotency"
    )
    claimed = jobs.claim_job(job["job_id"])
    assert claimed is not None

    intent_id = uuid4()
    candidate_id = uuid4()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_intents
                (intent_id, job_id, lease_token, stage,
                 file_id, input_fingerprint)
            VALUES (%s, %s, %s, 'BRONZE', %s, %s)
            """,
            (
                intent_id, job["job_id"], claimed["lease_token"],
                job["file_id"], "a" * 64,
            ),
        )
        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256,
                 status, verified_at)
            VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
            """,
            (candidate_id, intent_id, "b" * 64),
        )

    first = publication.publish_candidate(
        job["job_id"], claimed["lease_token"], candidate_id
    )

    second = publication.publish_candidate(
        job["job_id"], claimed["lease_token"], candidate_id
    )

    with connect() as conn:
        receipt_count = conn.execute(
            "SELECT COUNT(*) FROM processing_stage_receipts"
        ).fetchone()[0]

        head = conn.execute(
            """
            SELECT receipt_id, candidate_id
            FROM processing_stage_heads
            WHERE stage = 'BRONZE' AND file_id = %s
            """,
            (job["file_id"],),
        ).fetchone()

    assert receipt_count == 1
    assert head[1] == candidate_id
    assert head[0] == first["receipt_id"]

    # Both calls should identify the same original publication.
    assert first["receipt_id"] == second["receipt_id"]

def test_competing_jobs_cannot_replace_published_bronze(source):
    connect, user_id, upload_id = source

    def prepare(key, digest):
        job = jobs.create_job(user_id, upload_id, "BRONZE", key)
        claimed = jobs.claim_job(job["job_id"])
        assert claimed is not None

        candidate_id = uuid4()

        with connect() as conn:
            intent_id = conn.execute(
                """
                INSERT INTO processing_stage_intents
                    (job_id, lease_token, stage, file_id, input_fingerprint)
                VALUES (%s, %s, 'BRONZE', %s, %s)
                RETURNING intent_id
                """,
                (
                    job["job_id"],
                    claimed["lease_token"],
                    job["file_id"],
                    "a" * 64,
                ),
            ).fetchone()[0]

            conn.execute(
                """
                INSERT INTO processing_artifact_candidates
                    (candidate_id, intent_id, manifest_sha256,
                     status, verified_at)
                VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
                """,
                (candidate_id, intent_id, digest),
            )

        return job["job_id"], claimed["lease_token"], candidate_id

    first = prepare("competing-publication-a", "b" * 64)
    second = prepare("competing-publication-b", "c" * 64)

    def publish(prepared):
        try:
            return publication.publish_candidate(*prepared)
        except publication.ArtifactPublicationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(Context().run, publish, first),
            executor.submit(Context().run, publish, second),
        ]
        results = [future.result(timeout=30) for future in futures]

    assert sum(result is not None for result in results) == 1

    successful_candidate = next(
        result["candidate_id"]
        for result in results
        if result is not None
    )

    with connect() as conn:
        receipts = conn.execute(
            "SELECT candidate_id FROM processing_stage_receipts"
        ).fetchall()

        head = conn.execute(
            """
            SELECT candidate_id
            FROM processing_stage_heads
            WHERE stage = 'BRONZE'
              AND file_id = %s
              AND dataset_version_file_id IS NULL
            """,
            (first and jobs.create_job(
                user_id, upload_id, "BRONZE", "competing-publication-a"
            )["file_id"],),
        ).fetchone()

    assert len(receipts) == 1
    assert head is not None
    assert head[0] == successful_candidate
def test_receipt_cannot_be_assigned_to_different_stage_scope(source):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id, upload_id, "BRONZE", "cross-scope-test"
    )
    claimed = jobs.claim_job(job["job_id"])

    with connect() as conn:
        intent_id = conn.execute(
            """
            INSERT INTO processing_stage_intents
                (job_id, lease_token, stage, file_id, input_fingerprint)
            VALUES (%s, %s, 'BRONZE', %s, %s)
            RETURNING intent_id
            """,
            (
                job["job_id"],
                claimed["lease_token"],
                job["file_id"],
                "a" * 64,
            ),
        ).fetchone()[0]

        candidate_id = uuid4()

        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256,
                 status, verified_at)
            VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
            """,
            (candidate_id, intent_id, "b" * 64),
        )

    result = publication.publish_candidate(
        job["job_id"], claimed["lease_token"], candidate_id
    )

    with connect() as conn:
        other_file_id = conn.execute(
            """
            INSERT INTO physical_files
                (file_name, file_size, file_hash, storage_path, status)
            VALUES ('different.csv', 10, %s, 'test-only', 'UPLOADED')
            RETURNING file_id
            """,
            ("f" * 64,),
        ).fetchone()[0]

    # A valid receipt must not authorize another physical file.
    with pytest.raises(psycopg.Error):
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO processing_stage_heads
                    (stage, file_id, dataset_version_file_id,
                     receipt_id, candidate_id)
                VALUES ('BRONZE', %s, NULL, %s, %s)
                """,
                (
                    other_file_id,
                    result["receipt_id"],
                    candidate_id,
                ),
            )

@pytest.mark.parametrize(
    "revocation",
    ["workspace_owner", "archived_upload", "disabled_user"],
)
def test_publication_rejects_revoked_authorization(source, revocation):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id,
        upload_id,
        "BRONZE",
        f"publication-revoked-{revocation}",
    )
    claimed = jobs.claim_job(job["job_id"])
    assert claimed is not None

    intent_id = uuid4()
    candidate_id = uuid4()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_intents
                (intent_id, job_id, lease_token, stage,
                 file_id, input_fingerprint)
            VALUES (%s, %s, %s, 'BRONZE', %s, %s)
            """,
            (
                intent_id,
                job["job_id"],
                claimed["lease_token"],
                job["file_id"],
                "a" * 64,
            ),
        )

        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256,
                 status, verified_at)
            VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
            """,
            (candidate_id, intent_id, "b" * 64),
        )

        if revocation == "workspace_owner":
            conn.execute(
                """
                UPDATE workspaces
                SET owner = 'dev:revoked'
                WHERE workspace_id = %s
                """,
                (job["workspace_id"],),
            )
        elif revocation == "archived_upload":
            conn.execute(
                """
                UPDATE upload_requests
                SET archived_at = clock_timestamp(),
                    archived_by = %s
                WHERE upload_id = %s
                """,
                (user_id, upload_id),
            )
        else:
            conn.execute(
                """
                UPDATE users
                SET account_status = 'DISABLED'
                WHERE user_id = %s
                """,
                (user_id,),
            )

    with pytest.raises(publication.ArtifactPublicationError):
        publication.publish_candidate(
            job["job_id"],
            claimed["lease_token"],
            candidate_id,
        )

    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM processing_stage_receipts"
        ).fetchone()[0] == 0

        assert conn.execute(
            "SELECT COUNT(*) FROM processing_stage_heads"
        ).fetchone()[0] == 0
@pytest.mark.parametrize(
    "scenario",
    ["checksum_change", "restore_retired", "retire_published"],
)
def test_candidate_lifecycle_protection(source, scenario):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id, upload_id, "BRONZE",
        f"candidate-lifecycle-{scenario}",
    )
    claimed = jobs.claim_job(job["job_id"])
    assert claimed is not None

    candidate_id = uuid4()

    with connect() as conn:
        intent_id = conn.execute(
            """
            INSERT INTO processing_stage_intents
                (job_id, lease_token, stage, file_id, input_fingerprint)
            VALUES (%s, %s, 'BRONZE', %s, %s)
            RETURNING intent_id
            """,
            (
                job["job_id"], claimed["lease_token"],
                job["file_id"], "a" * 64,
            ),
        ).fetchone()[0]

        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256,
                 status, verified_at)
            VALUES (%s, %s, %s, 'VERIFIED', clock_timestamp())
            """,
            (candidate_id, intent_id, "b" * 64),
        )

    if scenario == "restore_retired":
        with connect() as conn:
            conn.execute(
                """
                UPDATE processing_artifact_candidates
                SET status = 'RETIRED'
                WHERE candidate_id = %s
                """,
                (candidate_id,),
            )

    if scenario == "retire_published":
        publication.publish_candidate(
            job["job_id"], claimed["lease_token"], candidate_id
        )

    with pytest.raises(psycopg.Error):
        with connect() as conn:
            if scenario == "checksum_change":
                conn.execute(
                    """
                    UPDATE processing_artifact_candidates
                    SET manifest_sha256 = %s
                    WHERE candidate_id = %s
                    """,
                    ("c" * 64, candidate_id),
                )
            elif scenario == "restore_retired":
                conn.execute(
                    """
                    UPDATE processing_artifact_candidates
                    SET status = 'VERIFIED'
                    WHERE candidate_id = %s
                    """,
                    (candidate_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE processing_artifact_candidates
                    SET status = 'RETIRED'
                    WHERE candidate_id = %s
                    """,
                    (candidate_id,),
                )

def test_valid_evidence_marks_candidate_verified(source):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id,
        upload_id,
        "BRONZE",
        "verified-transition-test",
    )
    claimed = jobs.claim_job(job["job_id"])
    assert claimed is not None

    intent_id = uuid4()
    candidate_id = uuid4()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_intents
                (intent_id, job_id, lease_token, stage,
                 file_id, input_fingerprint)
            VALUES (%s, %s, %s, 'BRONZE', %s, %s)
            """,
            (
                intent_id,
                job["job_id"],
                claimed["lease_token"],
                job["file_id"],
                "a" * 64,
            ),
        )

        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256)
            VALUES (%s, %s, %s)
            """,
            (candidate_id, intent_id, "b" * 64),
        )

    evidence = VerifiedCandidate(
        intent_id=intent_id,
        candidate_id=candidate_id,
        stage="BRONZE",
        input_fingerprint="a" * 64,
        manifest_sha256="b" * 64,
        artifact_count=1,
    )

    result = publication.mark_candidate_verified(
        job["job_id"],
        claimed["lease_token"],
        evidence,
    )

    assert result == candidate_id

    with connect() as conn:
        row = conn.execute(
            """
            SELECT status, verified_at
            FROM processing_artifact_candidates
            WHERE candidate_id = %s
            """,
            (candidate_id,),
        ).fetchone()

    assert row[0] == "VERIFIED"
    assert row[1] is not None

@pytest.mark.parametrize(
    "scenario",
    ["wrong_checksum", "wrong_fingerprint", "expired_lease", "wrong_token"],
)
def test_invalid_evidence_cannot_verify_candidate(source, scenario):
    connect, user_id, upload_id = source

    job = jobs.create_job(
        user_id, upload_id, "BRONZE", f"verification-reject-{scenario}"
    )
    claimed = jobs.claim_job(job["job_id"])
    assert claimed is not None

    intent_id = uuid4()
    candidate_id = uuid4()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_intents
                (intent_id, job_id, lease_token, stage,
                 file_id, input_fingerprint)
            VALUES (%s, %s, %s, 'BRONZE', %s, %s)
            """,
            (
                intent_id, job["job_id"], claimed["lease_token"],
                job["file_id"], "a" * 64,
            ),
        )
        conn.execute(
            """
            INSERT INTO processing_artifact_candidates
                (candidate_id, intent_id, manifest_sha256)
            VALUES (%s, %s, %s)
            """,
            (candidate_id, intent_id, "b" * 64),
        )

        if scenario == "expired_lease":
            conn.execute(
                """
                UPDATE processing_jobs
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE job_id = %s
                """,
                (job["job_id"],),
            )

    evidence = VerifiedCandidate(
        intent_id=intent_id,
        candidate_id=candidate_id,
        stage="BRONZE",
        input_fingerprint=(
            "c" * 64 if scenario == "wrong_fingerprint" else "a" * 64
        ),
        manifest_sha256=(
            "d" * 64 if scenario == "wrong_checksum" else "b" * 64
        ),
        artifact_count=1,
    )

    token = uuid4() if scenario == "wrong_token" else claimed["lease_token"]

    with pytest.raises(publication.ArtifactPublicationError):
        publication.mark_candidate_verified(job["job_id"], token, evidence)

    with connect() as conn:
        row = conn.execute(
            """
            SELECT status, verified_at
            FROM processing_artifact_candidates
            WHERE candidate_id = %s
            """,
            (candidate_id,),
        ).fetchone()

    assert row == ("STAGED", None)
