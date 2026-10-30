import hashlib

from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline, approve_first
import pytest
import psycopg
from app.services import processing_service as stages


def test_silver_success_persists_stored_artifact_sha256(
    pipeline,
    monkeypatch,
):
    db, _, _, result, objects, _ = pipeline

    approve_first(pipeline)

    # Activate the exact-source checksum branch using
    # the fixture's already-approved DECIMAL rule.
    # This tests checksum persistence, not physical
    # Exact Decimal serialization.
    monkeypatch.setattr(
        stages,
        "exact_decimal_spec",
        lambda config: (
            object()
            if isinstance(config, dict)
            and config.get("type") == "DECIMAL"
            else None
        ),
    )

    association_id = result["dataset_version_file"][0]

    silver = stages.run_silver_processing(
        association_id,
        "test",
    )

    assert silver["status"] == "SUCCESS"

    source_key = silver["silver_path"]
    expected_hash = hashlib.sha256(
        objects[source_key]
    ).hexdigest()

    with db[0]() as conn:
        saved = conn.execute(
            """
            SELECT silver_sha256
            FROM data_quality_runs
            WHERE dq_run_id = %s
            """,
            (silver["dq_run_id"],),
        ).fetchone()

    assert saved is not None
    assert saved[0] == expected_hash
def test_successful_silver_checksum_is_immutable(
    pipeline,
    monkeypatch,
):
    db, _, _, result, objects, _ = pipeline

    approve_first(pipeline)

    # Test override to exercise checksum persistence.
    # This is not a full genuine Exact Silver test.
    monkeypatch.setattr(
        stages,
        "exact_decimal_spec",
        lambda config: (
            object()
            if isinstance(config, dict)
            and config.get("type") == "DECIMAL"
            else None
        ),
    )

    association_id = result["dataset_version_file"][0]

    silver = stages.run_silver_processing(
        association_id,
        "test",
    )

    dq_id = silver["dq_run_id"]

    with db[0]() as conn:
        attempt_id, saved_hash = conn.execute(
            """
            SELECT attempt_id, silver_sha256
            FROM data_quality_runs
            WHERE dq_run_id = %s
            """,
            (dq_id,),
        ).fetchone()

    assert saved_hash is not None

    # A completed DQ checksum cannot be replaced.
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="immutable",
    ):
        with db[0]() as conn:
            conn.execute(
                """
                UPDATE data_quality_runs
                SET silver_sha256 = %s
                WHERE dq_run_id = %s
                """,
                ("0" * 64, dq_id),
            )

    # The successful attempt cannot be rolled back
    # to bypass the checksum protection.
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="immutable",
    ):
        with db[0]() as conn:
            conn.execute(
                """
                UPDATE processing_attempts
                SET status = 'FAILED'
                WHERE attempt_id = %s
                """,
                (attempt_id,),
            )

    # Nor can the successful DQ evidence be deleted.
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="immutable",
    ):
        with db[0]() as conn:
            conn.execute(
                """
                DELETE FROM data_quality_runs
                WHERE dq_run_id = %s
                """,
                (dq_id,),
            )