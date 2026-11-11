"""414F1 pre-send request fingerprint persistence on migrated disposable Postgres.

No BigQuery send, real provider credentials, roles, or physical cleanup.
"""
from pathlib import Path

import psycopg
import pytest

from app.services import warehouse_sql_provider_policy_service as policy
from app.services import warehouse_sql_send_recovery_service as recovery
from tests.test_warehouse_sql_provider_policy_414e3 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound,
    scalar, change, request,
)

MIGRATION = Path(__file__).resolve().parents[1] / (
    "migrations/0058_warehouse_query_request_envelopes.sql"
)


@pytest.fixture
def pinned_db(unbound):
    s, lease, client = unbound
    with s.f.pg["connect"]() as conn:
        assert conn.execute("SELECT current_schema()").fetchone()[0].startswith("test_warehouse_")
        conn.execute(MIGRATION.read_text(encoding="utf-8-sig"))
    return s, lease, client


def pin(s):
    return policy.pin_authenticated_query_envelope_without_dispatch(
        request=request(s.token),
        workspace_id=s.f.pg["workspace"],
        attempt_handle=s.a.attempt_handle,
    )


def test_authenticated_request_is_pinned_once_and_idempotent(pinned_db):
    s, lease, client = pinned_db
    first = pin(s)
    again = pin(s)
    assert first == again
    assert not first.execution_authorized and not first.provider_dispatched
    assert scalar(s, "SELECT count(*) FROM warehouse_query_request_envelopes") == 1
    with s.f.pg["connect"]() as conn:
        row = conn.execute(
            """SELECT e.provider_job_id,e.request_sha256,e.policy_sha256,p.expected_sha256
               FROM warehouse_query_request_envelopes e
               JOIN warehouse_query_provider_policies p USING(attempt_id)"""
        ).fetchone()
        assert row[0] == first.job_id and row[1] == first.request_sha256
        assert row[2] == row[3]
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0
    assert not client.calls


def test_pinned_request_cannot_be_mutated_deleted_or_truncated(pinned_db):
    s, lease, client = pinned_db
    pin(s)
    with s.f.pg["connect"]() as conn:
        for statement in (
            "DELETE FROM warehouse_query_request_envelopes",
            "UPDATE warehouse_query_request_envelopes SET request_sha256=repeat('f',64)",
            "TRUNCATE warehouse_query_request_envelopes",
        ):
            with pytest.raises(psycopg.DatabaseError):
                with conn.transaction():
                    conn.execute(statement)
    assert scalar(s, "SELECT count(*) FROM warehouse_query_request_envelopes") == 1
    assert not client.calls


def test_send_boundary_forbids_new_request_pin_but_preserves_original(pinned_db):
    s, lease, client = pinned_db
    original = pin(s)
    recovery.record_send_boundary(lease=lease, expected_revision=lease.revision)
    with pytest.raises(policy.PolicyConflict, match="EXISTING_SEND_STATE"):
        pin(s)
    assert scalar(s, "SELECT count(*) FROM warehouse_query_request_envelopes") == 1
    assert original.request_sha256
    assert not client.calls


def test_revocation_denies_new_pin_and_does_not_touch_provider(pinned_db):
    s, lease, client = pinned_db
    change(s, "account")
    with pytest.raises(Exception):
        pin(s)
    assert scalar(s, "SELECT count(*) FROM warehouse_query_request_envelopes") == 0
    assert not client.calls
