"""11H durable query-governance reservations in isolated PostgreSQL schemas."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import psycopg
import pytest

from app.db import query_governance_repository as repository
from app.query_governance.policy import GIB, MIB, QueryPolicy
from app.query_governance.types import (
    Decision,
    QueryEstimate,
)
from tests.test_warehouse_foundation import pg


ROOT = Path(__file__).resolve().parents[1]

MIGRATION = ROOT / "migrations/0032_query_governance.sql"
ROLLBACK = ROOT / "migrations/rollback/0032_query_governance.sql"

RACE_POLICY = QueryPolicy(
    auto_execute_bytes=256 * MIB,
    hard_bytes=256 * MIB,
    workspace_daily_bytes=500 * MIB,
    project_monthly_bytes=1 * GIB,
    workspace_concurrency=4,
    global_concurrency=8,
)


@pytest.fixture
def governance_pg(pg):
    with pg["connect"]() as conn:
        conn.execute(
            (ROOT / "migrations/0030_warehouse_modeling.sql").read_text(
                encoding="utf-8"
            )
        )

        conn.execute(
            (ROOT / "migrations/0031_warehouse_reliability.sql").read_text(
                encoding="utf-8"
            )
        )

        before = {
            table: conn.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            for table in (
                "users",
                "workspaces",
                "datasets",
                "warehouse_models",
                "warehouse_operations",
            )
        }

        conn.execute(MIGRATION.read_text(encoding="utf-8"))

        after = {
            table: conn.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            for table in before
        }

        assert before == after

    return pg


def reserve(
    conn,
    fixture,
    token,
    *,
    intent="f",
    estimated=100 * MIB,
    workspace=None,
    owner=None,
    policy=None,
):
    return repository.reserve(
        conn,
        workspace_id=workspace or fixture["workspace"],
        owner_key=(owner or fixture["owner"])["owner_key"],
        consumer="SQL",
        provider="BIGQUERY",
        request_token=token * 64,
        intent_sha256=intent * 64,
        estimate=QueryEstimate(estimated),
        policy=policy or QueryPolicy(),
    )


def test_migration_creates_empty_governance_tables(governance_pg):
    with governance_pg["connect"]() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname=current_schema()
                """
            )
        }

        assert "query_governance_reservations" in tables
        assert "query_governance_usage" in tables

        assert conn.execute(
            "SELECT count(*) FROM query_governance_reservations"
        ).fetchone()[0] == 0

        assert conn.execute(
            "SELECT count(*) FROM query_governance_usage"
        ).fetchone()[0] == 0


def test_review_and_blocked_decisions_do_not_reserve(governance_pg):
    with governance_pg["connect"]() as conn:
        review = reserve(
            conn,
            governance_pg,
            "a",
            estimated=300 * MIB,
        )

        blocked = reserve(
            conn,
            governance_pg,
            "b",
            estimated=GIB + 1,
        )

        assert review["decision"].decision == Decision.REVIEW_REQUIRED
        assert blocked["decision"].decision == Decision.BLOCKED

        assert conn.execute(
            "SELECT count(*) FROM query_governance_reservations"
        ).fetchone()[0] == 0


def test_allowed_reservation_is_idempotent_but_contract_is_pinned(
    governance_pg,
):
    with governance_pg["connect"]() as conn:
        first = reserve(
            conn,
            governance_pg,
            "a",
            estimated=100 * MIB,
        )

        again = reserve(
            conn,
            governance_pg,
            "a",
            estimated=100 * MIB,
        )

        assert first["decision"].decision == Decision.ALLOWED
        assert not first["reused"]

        assert again["reused"]
        assert again["decision"] is None

        assert (
            again["reservation"]["reservation_id"]
            == first["reservation"]["reservation_id"]
        )

        with pytest.raises(
            repository.ReservationConflict,
            match="REQUEST_TOKEN_CONFLICT",
        ):
            reserve(
                conn,
                governance_pg,
                "a",
                estimated=101 * MIB,
            )


def test_completion_is_atomic_conservative_and_idempotent(governance_pg):
    with governance_pg["connect"]() as conn:
        admitted = reserve(
            conn,
            governance_pg,
            "a",
            estimated=100 * MIB,
        )

        reservation_id = admitted["reservation"]["reservation_id"]

        repository.claim(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
        )

        usage = repository.complete(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
            bytes_processed=120 * MIB,
            bytes_billed=130 * MIB,
            provider_job_id="synthetic-job-1",
            duration_ms=1234,
        )

        assert usage["accounted_bytes"] == 130 * MIB

    with governance_pg["connect"]() as conn:
        row = conn.execute(
            """
            SELECT status, finalized_at
            FROM query_governance_reservations
            WHERE reservation_id=%s
            """,
            (reservation_id,),
        ).fetchone()

        assert row[0] == "COMPLETED"
        assert row[1] is not None

        again = repository.complete(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
            bytes_processed=120 * MIB,
            bytes_billed=130 * MIB,
            provider_job_id="synthetic-job-1",
            duration_ms=1234,
        )

        assert again["usage_id"] == usage["usage_id"]

        with pytest.raises(
            repository.ReservationConflict,
            match="COMPLETION_CONFLICT",
        ):
            repository.complete(
                conn,
                reservation_id=reservation_id,
                owner_key=governance_pg["owner"]["owner_key"],
                bytes_processed=121 * MIB,
                bytes_billed=130 * MIB,
                provider_job_id="synthetic-job-1",
                duration_ms=1234,
            )


def test_release_is_idempotent_and_creates_no_usage(governance_pg):
    with governance_pg["connect"]() as conn:
        admitted = reserve(conn, governance_pg, "a")
        reservation_id = admitted["reservation"]["reservation_id"]

        first = repository.release(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
        )

        second = repository.release(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
        )

        assert first["status"] == "RELEASED"
        assert second["status"] == "RELEASED"

        assert conn.execute(
            """
            SELECT count(*)
            FROM query_governance_usage
            WHERE reservation_id=%s
            """,
            (reservation_id,),
        ).fetchone()[0] == 0


def test_project_monthly_usage_is_shared_across_workspaces(governance_pg):
    with governance_pg["connect"]() as conn:
        admitted = reserve(
            conn,
            governance_pg,
            "a",
            estimated=100 * MIB,
        )

        reservation_id = admitted["reservation"]["reservation_id"]

        repository.claim(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
        )

        repository.complete(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
            bytes_processed=100 * MIB,
            bytes_billed=100 * MIB,
            provider_job_id="synthetic-job-1",
            duration_ms=10,
        )

    with governance_pg["connect"]() as conn:
        snapshot = repository.usage_snapshot(
            conn,
            governance_pg["foreign_workspace"],
            governance_pg["other"]["owner_key"],
        )

        assert snapshot.committed_bytes_today == 0
        assert snapshot.project_committed_bytes_month == 100 * MIB


def test_global_concurrency_is_cross_workspace(governance_pg):
    policy = QueryPolicy(
        workspace_concurrency=1,
        global_concurrency=1,
    )

    with governance_pg["connect"]() as conn:
        first = reserve(
            conn,
            governance_pg,
            "a",
            policy=policy,
        )

        assert first["decision"].decision == Decision.ALLOWED

    with governance_pg["connect"]() as conn:
        second = reserve(
            conn,
            governance_pg,
            "b",
            intent="e",
            workspace=governance_pg["foreign_workspace"],
            owner=governance_pg["other"],
            policy=policy,
        )

        assert second["decision"].decision == Decision.BLOCKED
        assert (
            second["decision"].reason
            == "GLOBAL_CONCURRENCY_LIMIT"
        )


def test_concurrent_workers_cannot_oversubscribe_workspace_budget(
    governance_pg,
):
    with governance_pg["connect"]() as conn:
        initial = reserve(
            conn,
            governance_pg,
            "a",
            estimated=200 * MIB,
            policy=RACE_POLICY,
        )
        repository.claim(
            conn,
            reservation_id=initial["reservation"]["reservation_id"],
            owner_key=governance_pg["owner"]["owner_key"],
        )

        repository.complete(
            conn,
            reservation_id=initial["reservation"]["reservation_id"],
            owner_key=governance_pg["owner"]["owner_key"],
            bytes_processed=200 * MIB,
            bytes_billed=200 * MIB,
            provider_job_id="initial-job",
            duration_ms=10,
        )

    barrier = Barrier(2)

    def worker(token, intent):
        with governance_pg["connect"]() as conn:
            barrier.wait()

            result = reserve(
                conn,
                governance_pg,
                token,
                intent=intent,
                estimated=200 * MIB,
                policy=RACE_POLICY,
            )

            return (
                result["decision"].decision,
                result["decision"].reason,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(worker, "b", "c"),
            pool.submit(worker, "d", "e"),
        ]

        results = [future.result() for future in futures]

    decisions = [value[0] for value in results]
    reasons = [value[1] for value in results]

    assert decisions.count(Decision.ALLOWED) == 1
    assert decisions.count(Decision.BLOCKED) == 1

    assert "WORKSPACE_DAILY_BUDGET_EXCEEDED" in reasons

    with governance_pg["connect"]() as conn:
        snapshot = repository.usage_snapshot(
            conn,
            governance_pg["workspace"],
            governance_pg["owner"]["owner_key"],
        )

        assert snapshot.committed_bytes_today == 200 * MIB
        assert snapshot.reserved_bytes_today == 200 * MIB


def test_database_rejects_inconsistent_or_destructive_state(
    governance_pg,
):
    with governance_pg["connect"]() as conn:
        admitted = reserve(conn, governance_pg, "a")
        reservation_id = admitted["reservation"]["reservation_id"]

    with pytest.raises(psycopg.Error):
        with governance_pg["connect"]() as conn:
            conn.execute(
                """
                UPDATE query_governance_reservations
                SET status='COMPLETED',
                    finalized_at=clock_timestamp()
                WHERE reservation_id=%s
                """,
                (reservation_id,),
            )

    for statement in (
        "DELETE FROM query_governance_reservations",
        "TRUNCATE query_governance_reservations",
        "TRUNCATE query_governance_usage",
    ):
        with pytest.raises(psycopg.Error):
            with governance_pg["connect"]() as conn:
                conn.execute(statement)


def test_empty_rollback_removes_only_governance_objects(governance_pg):
    with governance_pg["connect"]() as conn:
        conn.execute(ROLLBACK.read_text(encoding="utf-8"))

        assert conn.execute(
            "SELECT to_regclass('query_governance_reservations')"
        ).fetchone()[0] is None

        assert conn.execute(
            "SELECT to_regclass('query_governance_usage')"
        ).fetchone()[0] is None

        assert conn.execute(
            "SELECT count(*) FROM workspaces"
        ).fetchone()[0] == 2


def test_populated_rollback_refuses_and_preserves_history(governance_pg):
    with governance_pg["connect"]() as conn:
        reserve(conn, governance_pg, "a")

    with pytest.raises(psycopg.Error):
        with governance_pg["connect"]() as conn:
            conn.execute(
                ROLLBACK.read_text(encoding="utf-8")
            )

    with governance_pg["connect"]() as conn:
        assert conn.execute(
            """
            SELECT count(*)
            FROM query_governance_reservations
            """
        ).fetchone()[0] == 1
def test_claim_is_single_worker_execution_boundary(governance_pg):
    with governance_pg["connect"]() as conn:
        admitted = reserve(conn, governance_pg, "a")
        reservation_id = admitted["reservation"]["reservation_id"]

        claimed = repository.claim(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
        )

        assert claimed["status"] == "EXECUTING"

        with pytest.raises(
            repository.ReservationConflict,
            match="RESERVATION_ALREADY_CLAIMED",
        ):
            repository.claim(
                conn,
                reservation_id=reservation_id,
                owner_key=governance_pg["owner"]["owner_key"],
            )


def test_unclaimed_reservation_cannot_be_completed(governance_pg):
    with governance_pg["connect"]() as conn:
        admitted = reserve(conn, governance_pg, "a")

        with pytest.raises(
            repository.ReservationConflict,
            match="RESERVATION_NOT_EXECUTING",
        ):
            repository.complete(
                conn,
                reservation_id=admitted["reservation"]["reservation_id"],
                owner_key=governance_pg["owner"]["owner_key"],
                bytes_processed=1,
                bytes_billed=1,
                provider_job_id="must-not-complete",
                duration_ms=1,
            )

def test_expired_executing_query_still_consumes_capacity(governance_pg):
    with governance_pg["connect"]() as conn:
        admitted = repository.reserve(
            conn,
            workspace_id=governance_pg["workspace"],
            owner_key=governance_pg["owner"]["owner_key"],
            consumer="SQL",
            provider="BIGQUERY",
            request_token="9" * 64,
            intent_sha256="8" * 64,
            estimate=QueryEstimate(100 * MIB),
            policy=QueryPolicy(),
            lease_seconds=1,
        )

        reservation_id = admitted["reservation"]["reservation_id"]

        repository.claim(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
        )

    # Database time is authoritative. Expiry is deliberately allowed to pass.
    with governance_pg["connect"]() as conn:
        conn.execute("SELECT pg_sleep(1.1)")

    with governance_pg["connect"]() as conn:
        snapshot = repository.usage_snapshot(
            conn,
            governance_pg["workspace"],
            governance_pg["owner"]["owner_key"],
        )

        assert snapshot.reserved_bytes_today == 100 * MIB
        assert snapshot.project_reserved_bytes_month == 100 * MIB
        assert snapshot.workspace_active_queries == 1
        assert snapshot.global_active_queries == 1

        with pytest.raises(
            repository.ReservationConflict,
            match="EXECUTION_RECOVERY_REQUIRED",
        ):
            repository.claim(
                conn,
                reservation_id=reservation_id,
                owner_key=governance_pg["owner"]["owner_key"],
            )
def test_failed_execution_with_actual_usage_is_terminal(governance_pg):
    with governance_pg["connect"]() as conn:
        admitted = reserve(
            conn,
            governance_pg,
            "6",
            estimated=100 * MIB,
        )

        reservation_id = admitted["reservation"]["reservation_id"]

        repository.claim(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
        )

        usage = repository.fail(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
            bytes_processed=40 * MIB,
            bytes_billed=50 * MIB,
            provider_job_id="failed-job",
            duration_ms=700,
        )

        assert usage["outcome"] == "FAILED"
        assert usage["accounting_basis"] == "ACTUAL"
        assert usage["accounted_bytes"] == 50 * MIB

    with governance_pg["connect"]() as conn:
        snapshot = repository.usage_snapshot(
            conn,
            governance_pg["workspace"],
            governance_pg["owner"]["owner_key"],
        )

        assert snapshot.committed_bytes_today == 50 * MIB
        assert snapshot.reserved_bytes_today == 0
        assert snapshot.workspace_active_queries == 0


def test_failed_execution_without_stats_uses_reserved_fallback(
    governance_pg,
):
    with governance_pg["connect"]() as conn:
        admitted = reserve(
            conn,
            governance_pg,
            "7",
            estimated=100 * MIB,
        )

        reservation_id = admitted["reservation"]["reservation_id"]

        repository.claim(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
        )

        usage = repository.fail(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
            bytes_processed=None,
            bytes_billed=None,
            provider_job_id="failed-job-no-stats",
            duration_ms=300,
        )

        assert usage["outcome"] == "FAILED"
        assert (
            usage["accounting_basis"]
            == "RESERVED_FALLBACK"
        )
        assert usage["accounted_bytes"] == 100 * MIB


def test_failed_execution_finalization_is_idempotent(governance_pg):
    with governance_pg["connect"]() as conn:
        admitted = reserve(conn, governance_pg, "8")
        reservation_id = admitted["reservation"]["reservation_id"]

        repository.claim(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
        )

        first = repository.fail(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
            bytes_processed=None,
            bytes_billed=None,
            provider_job_id="failed-idempotent",
            duration_ms=10,
        )

    with governance_pg["connect"]() as conn:
        again = repository.fail(
            conn,
            reservation_id=reservation_id,
            owner_key=governance_pg["owner"]["owner_key"],
            bytes_processed=None,
            bytes_billed=None,
            provider_job_id="failed-idempotent",
            duration_ms=10,
        )

        assert again["usage_id"] == first["usage_id"]

        with pytest.raises(
            repository.ReservationConflict,
            match="FAILURE_CONFLICT",
        ):
            repository.fail(
                conn,
                reservation_id=reservation_id,
                owner_key=governance_pg["owner"]["owner_key"],
                bytes_processed=1,
                bytes_billed=1,
                provider_job_id="different",
                duration_ms=10,
            )