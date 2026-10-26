"""Durable cross-process query admission reservations and usage accounting."""

import re

from psycopg.rows import dict_row

from app.query_governance.admission import decide
from app.query_governance.policy import QueryPolicy
from app.query_governance.types import (
    Decision,
    QueryEstimate,
    UsageSnapshot,
)


TOKEN = re.compile(r"^[a-f0-9]{64}$")
CONSUMERS = {"SQL", "ANALYTICS", "ASK", "METRIC"}
PROVIDERS = {"DUCKDB", "BIGQUERY"}


class ReservationConflict(ValueError):
    pass


def read_owned_reservation(conn, *, workspace_id, owner_key, reservation_id):
    """Recovery reads retain the same current-owner and reservation-owner boundary."""
    _lock_scopes(conn, workspace_id)
    _require_owner(conn, workspace_id, owner_key)
    row = conn.cursor(row_factory=dict_row).execute(
        '''SELECT * FROM query_governance_reservations
           WHERE reservation_id=%s AND workspace_id=%s AND owner_key=%s''',
        (reservation_id, workspace_id, owner_key)).fetchone()
    if not row:
        raise ReservationConflict('RESERVATION_NOT_FOUND')
    return row


def _lock_scopes(conn, workspace_id: int):
    # Always global first, workspace second: deterministic lock order.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("datarise-query-governance-global",),
    )

    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"datarise-query-governance-workspace:{workspace_id}",),
    )


def _require_owner(conn, workspace_id: int, owner_key: str):
    row = conn.execute(
        """
        SELECT workspace_id
        FROM workspaces
        WHERE workspace_id=%s
          AND owner=%s
        """,
        (workspace_id, owner_key),
    ).fetchone()

    if not row:
        raise ReservationConflict("WORKSPACE_NOT_OWNED")


def _snapshot_locked(conn, workspace_id: int) -> UsageSnapshot:
    row = conn.cursor(row_factory=dict_row).execute(
        """
        WITH bounds AS (
            SELECT
                date_trunc(
                    'day',
                    clock_timestamp() AT TIME ZONE 'UTC'
                ) AT TIME ZONE 'UTC' AS day_start,

                date_trunc(
                    'month',
                    clock_timestamp() AT TIME ZONE 'UTC'
                ) AT TIME ZONE 'UTC' AS month_start
        )
        SELECT
            COALESCE((
                SELECT sum(u.accounted_bytes)
                FROM query_governance_usage u, bounds b
                WHERE u.workspace_id=%s
                  AND u.accounted_at>=b.day_start
            ),0)::bigint AS committed_bytes_today,

            COALESCE((
                SELECT sum(r.reserved_bytes)
                FROM query_governance_reservations r
                WHERE r.workspace_id=%s
                  AND (
                      r.status='EXECUTING'
                      OR (
                          r.status='ACTIVE'
                          AND r.expires_at>clock_timestamp()
                      )
                  )
            ),0)::bigint AS reserved_bytes_today,

            COALESCE((
                SELECT sum(u.accounted_bytes)
                FROM query_governance_usage u, bounds b
                WHERE u.accounted_at>=b.month_start
            ),0)::bigint AS project_committed_bytes_month,

            COALESCE((
                SELECT sum(r.reserved_bytes)
                FROM query_governance_reservations r
                WHERE (
                      r.status='EXECUTING'
                      OR (
                          r.status='ACTIVE'
                          AND r.expires_at>clock_timestamp()
                      )
                  )
            ),0)::bigint AS project_reserved_bytes_month,

            (
                SELECT count(*)
                FROM query_governance_reservations r
                WHERE r.workspace_id=%s
                  AND (
                      r.status='EXECUTING'
                      OR (
                          r.status='ACTIVE'
                          AND r.expires_at>clock_timestamp()
                      )
                  )
            )::bigint AS workspace_active_queries,

            (
                SELECT count(*)
                FROM query_governance_reservations r
                WHERE (
                      r.status='EXECUTING'
                      OR (
                          r.status='ACTIVE'
                          AND r.expires_at>clock_timestamp()
                      )
                  )
            )::bigint AS global_active_queries
        """,
        (
            workspace_id,
            workspace_id,
            workspace_id,
        ),
    ).fetchone()

    return UsageSnapshot(**row)


def usage_snapshot(
    conn,
    workspace_id: int,
    owner_key: str,
) -> UsageSnapshot:
    _lock_scopes(conn, workspace_id)
    _require_owner(conn, workspace_id, owner_key)
    return _snapshot_locked(conn, workspace_id)


def reserve(
    conn,
    *,
    workspace_id: int,
    owner_key: str,
    consumer: str,
    provider: str,
    request_token: str,
    intent_sha256: str,
    estimate: QueryEstimate,
    requested_rows: int | None = None,
    policy: QueryPolicy | None = None,
    lease_seconds: int = 120,
):
    policy = policy or QueryPolicy()

    if not TOKEN.fullmatch(request_token):
        raise ValueError("Invalid request token")

    if not TOKEN.fullmatch(intent_sha256):
        raise ValueError("Invalid intent hash")

    if consumer not in CONSUMERS:
        raise ValueError("Invalid query consumer")

    if provider not in PROVIDERS:
        raise ValueError("Invalid query provider")

    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 900:
        raise ValueError("Invalid reservation lease")

    _lock_scopes(conn, workspace_id)
    _require_owner(conn, workspace_id, owner_key)

    existing = conn.cursor(row_factory=dict_row).execute(
        """
        SELECT *,
            (
                status IN ('ACTIVE','EXECUTING')
                AND expires_at<=clock_timestamp()
            ) AS expired
        FROM query_governance_reservations
        WHERE request_token=%s
        FOR UPDATE
        """,
        (request_token,),
    ).fetchone()

    if existing:
        expected_output_rows = (
            policy.default_output_rows
            if requested_rows is None
            else requested_rows
        )

        expected = (
            workspace_id,
            owner_key,
            consumer,
            provider,
            intent_sha256,
            estimate.bytes_processed,
            estimate.confidence.value,
            policy.auto_execute_bytes,
            expected_output_rows,
        )

        actual = (
            existing["workspace_id"],
            existing["owner_key"],
            existing["consumer"],
            existing["provider"],
            existing["intent_sha256"],
            existing["estimated_bytes"],
            existing["estimate_confidence"],
            existing["maximum_bytes_billed"],
            existing["output_rows"],
        )

        if actual != expected:
            raise ReservationConflict("REQUEST_TOKEN_CONFLICT")

        return {
            "decision": None,
            "reservation": existing,
            "usage": _snapshot_locked(conn, workspace_id),
            "reused": True,
        }

    usage = _snapshot_locked(conn, workspace_id)

    admission = decide(
        estimate,
        usage,
        requested_rows=requested_rows,
        policy=policy,
    )

    if admission.decision != Decision.ALLOWED:
        return {
            "decision": admission,
            "reservation": None,
            "usage": usage,
            "reused": False,
        }

    row = conn.cursor(row_factory=dict_row).execute(
        """
        INSERT INTO query_governance_reservations(
            request_token,
            intent_sha256,
            workspace_id,
            owner_key,
            consumer,
            provider,
            estimated_bytes,
            estimate_confidence,
            reserved_bytes,
            maximum_bytes_billed,
            output_rows,
            expires_at
        )
        VALUES (
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
            clock_timestamp() + (%s * interval '1 second')
        )
        RETURNING *
        """,
        (
            request_token,
            intent_sha256,
            workspace_id,
            owner_key,
            consumer,
            provider,
            admission.estimated_bytes,
            estimate.confidence.value,
            admission.reserved_bytes,
            admission.maximum_bytes_billed,
            admission.output_rows,
            lease_seconds,
        ),
    ).fetchone()

    return {
        "decision": admission,
        "reservation": row,
        "usage": usage,
        "reused": False,
    }


def complete(
    conn,
    *,
    reservation_id: int,
    owner_key: str,
    bytes_processed: int,
    bytes_billed: int,
    provider_job_id: str | None,
    duration_ms: int,
):
    for value in (
        bytes_processed,
        bytes_billed,
        duration_ms,
    ):
        if type(value) is not int or value < 0:
            raise ValueError("Usage values must be non-negative integers")

    initial = conn.cursor(row_factory=dict_row).execute(
        """
        SELECT workspace_id
        FROM query_governance_reservations
        WHERE reservation_id=%s
        """,
        (reservation_id,),
    ).fetchone()

    if not initial:
        raise ReservationConflict("RESERVATION_NOT_FOUND")

    workspace_id = initial["workspace_id"]

    _lock_scopes(conn, workspace_id)
    _require_owner(conn, workspace_id, owner_key)

    reservation = conn.cursor(row_factory=dict_row).execute(
        """
        SELECT *
        FROM query_governance_reservations
        WHERE reservation_id=%s
        FOR UPDATE
        """,
        (reservation_id,),
    ).fetchone()

    if not reservation or reservation["owner_key"] != owner_key:
        raise ReservationConflict("RESERVATION_NOT_FOUND")

    accounted = max(bytes_processed, bytes_billed)

    if reservation["status"] == "COMPLETED":
        usage = conn.cursor(row_factory=dict_row).execute(
            """
            SELECT *
            FROM query_governance_usage
            WHERE reservation_id=%s
            """,
            (reservation_id,),
        ).fetchone()

        expected = (
            bytes_processed,
            bytes_billed,
            accounted,
            provider_job_id,
            duration_ms,
        )

        actual = (
            usage["bytes_processed"],
            usage["bytes_billed"],
            usage["accounted_bytes"],
            usage["provider_job_id"],
            usage["duration_ms"],
        )

        if actual != expected:
            raise ReservationConflict("COMPLETION_CONFLICT")

        return usage

    if reservation["status"] != "EXECUTING":
        raise ReservationConflict("RESERVATION_NOT_EXECUTING")

    usage = conn.cursor(row_factory=dict_row).execute(
        """
        INSERT INTO query_governance_usage(
            reservation_id,
            workspace_id,
            owner_key,
            outcome,
            accounting_basis,
            bytes_processed,
            bytes_billed,
            accounted_bytes,
            provider_job_id,
            duration_ms
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        (
            reservation_id,
            workspace_id,
            owner_key,
            "SUCCEEDED",
            "ACTUAL",
            bytes_processed,
            bytes_billed,
            accounted,
            provider_job_id,
            duration_ms,
        ),
    ).fetchone()

    conn.execute(
        """
        UPDATE query_governance_reservations
        SET status='COMPLETED',
            finalized_at=clock_timestamp()
        WHERE reservation_id=%s
        """,
        (reservation_id,),
    )

    return usage


def release(
    conn,
    *,
    reservation_id: int,
    owner_key: str,
):
    initial = conn.cursor(row_factory=dict_row).execute(
        """
        SELECT workspace_id
        FROM query_governance_reservations
        WHERE reservation_id=%s
        """,
        (reservation_id,),
    ).fetchone()

    if not initial:
        raise ReservationConflict("RESERVATION_NOT_FOUND")

    workspace_id = initial["workspace_id"]

    _lock_scopes(conn, workspace_id)
    _require_owner(conn, workspace_id, owner_key)

    row = conn.cursor(row_factory=dict_row).execute(
        """
        SELECT *
        FROM query_governance_reservations
        WHERE reservation_id=%s
        FOR UPDATE
        """,
        (reservation_id,),
    ).fetchone()

    if not row or row["owner_key"] != owner_key:
        raise ReservationConflict("RESERVATION_NOT_FOUND")

    if row["status"] == "RELEASED":
        return row

    if row["status"] != "ACTIVE":
        raise ReservationConflict("RESERVATION_NOT_ACTIVE")

    return conn.cursor(row_factory=dict_row).execute(
        """
        UPDATE query_governance_reservations
        SET status='RELEASED',
            finalized_at=clock_timestamp()
        WHERE reservation_id=%s
        RETURNING *
        """,
        (reservation_id,),
    ).fetchone()


def claim(
    conn,
    *,
    reservation_id: int,
    owner_key: str,
):
    initial = conn.cursor(row_factory=dict_row).execute(
        """
        SELECT workspace_id
        FROM query_governance_reservations
        WHERE reservation_id=%s
        """,
        (reservation_id,),
    ).fetchone()

    if not initial:
        raise ReservationConflict("RESERVATION_NOT_FOUND")

    workspace_id = initial["workspace_id"]

    _lock_scopes(conn, workspace_id)
    _require_owner(conn, workspace_id, owner_key)

    reservation = conn.cursor(row_factory=dict_row).execute(
        """
        SELECT *,
            expires_at<=clock_timestamp() AS expired
        FROM query_governance_reservations
        WHERE reservation_id=%s
        FOR UPDATE
        """,
        (reservation_id,),
    ).fetchone()

    if not reservation or reservation["owner_key"] != owner_key:
        raise ReservationConflict("RESERVATION_NOT_FOUND")

    if reservation["status"] == "EXECUTING":
        if reservation["expired"]:
            raise ReservationConflict("EXECUTION_RECOVERY_REQUIRED")

        raise ReservationConflict("RESERVATION_ALREADY_CLAIMED")

    if reservation["status"] != "ACTIVE":
        raise ReservationConflict("RESERVATION_NOT_ACTIVE")

    if reservation["expired"]:
        raise ReservationConflict("RESERVATION_EXPIRED")

    return conn.cursor(row_factory=dict_row).execute(
        """
        UPDATE query_governance_reservations
        SET status='EXECUTING',
            execution_started_at=clock_timestamp()
        WHERE reservation_id=%s
        RETURNING *
        """,
        (reservation_id,),
    ).fetchone()


def fail(
    conn,
    *,
    reservation_id: int,
    owner_key: str,
    bytes_processed: int | None,
    bytes_billed: int | None,
    provider_job_id: str | None,
    duration_ms: int,
):
    for value in (bytes_processed, bytes_billed):
        if value is not None and (
            type(value) is not int or value < 0
        ):
            raise ValueError(
                "Provider byte values must be non-negative integers or None"
            )

    if type(duration_ms) is not int or duration_ms < 0:
        raise ValueError(
            "Duration must be a non-negative integer"
        )

    initial = conn.cursor(row_factory=dict_row).execute(
        """
        SELECT workspace_id
        FROM query_governance_reservations
        WHERE reservation_id=%s
        """,
        (reservation_id,),
    ).fetchone()

    if not initial:
        raise ReservationConflict("RESERVATION_NOT_FOUND")

    workspace_id = initial["workspace_id"]

    _lock_scopes(conn, workspace_id)
    _require_owner(conn, workspace_id, owner_key)

    reservation = conn.cursor(row_factory=dict_row).execute(
        """
        SELECT *
        FROM query_governance_reservations
        WHERE reservation_id=%s
        FOR UPDATE
        """,
        (reservation_id,),
    ).fetchone()

    if not reservation or reservation["owner_key"] != owner_key:
        raise ReservationConflict("RESERVATION_NOT_FOUND")

    if (
        bytes_processed is not None
        and bytes_billed is not None
    ):
        basis = "ACTUAL"
        accounted = max(
            bytes_processed,
            bytes_billed,
        )
    else:
        basis = "RESERVED_FALLBACK"
        accounted = max(
            reservation["reserved_bytes"],
            bytes_processed or 0,
            bytes_billed or 0,
        )

    expected = (
        "FAILED",
        basis,
        bytes_processed,
        bytes_billed,
        accounted,
        provider_job_id,
        duration_ms,
    )

    if reservation["status"] == "FAILED":
        usage = conn.cursor(row_factory=dict_row).execute(
            """
            SELECT *
            FROM query_governance_usage
            WHERE reservation_id=%s
            """,
            (reservation_id,),
        ).fetchone()

        actual = (
            usage["outcome"],
            usage["accounting_basis"],
            usage["bytes_processed"],
            usage["bytes_billed"],
            usage["accounted_bytes"],
            usage["provider_job_id"],
            usage["duration_ms"],
        )

        if actual != expected:
            raise ReservationConflict("FAILURE_CONFLICT")

        return usage

    if reservation["status"] != "EXECUTING":
        raise ReservationConflict(
            "RESERVATION_NOT_EXECUTING"
        )

    usage = conn.cursor(row_factory=dict_row).execute(
        """
        INSERT INTO query_governance_usage(
            reservation_id,
            workspace_id,
            owner_key,
            outcome,
            accounting_basis,
            bytes_processed,
            bytes_billed,
            accounted_bytes,
            provider_job_id,
            duration_ms
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        (
            reservation_id,
            workspace_id,
            owner_key,
            "FAILED",
            basis,
            bytes_processed,
            bytes_billed,
            accounted,
            provider_job_id,
            duration_ms,
        ),
    ).fetchone()

    conn.execute(
        """
        UPDATE query_governance_reservations
        SET status='FAILED',
            finalized_at=clock_timestamp()
        WHERE reservation_id=%s
        """,
        (reservation_id,),
    )

    return usage
