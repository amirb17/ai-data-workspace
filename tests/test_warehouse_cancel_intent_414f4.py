"""414F4 cancel-intent lifecycle against disposable PostgreSQL; no cloud."""
from pathlib import Path
from uuid import uuid4
from dataclasses import replace

import psycopg
import pytest

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_send_recovery_service as recovery
from app.services import warehouse_sql_cancel_intent_service as cancellation
from tests.test_warehouse_query_envelope_pinning_414f1 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    pin, scalar,
)

MIGRATION=Path(__file__).resolve().parents[1]/"migrations/0059_warehouse_query_cancel_intents.sql"


@pytest.fixture
def cancel_db(pinned_db):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        assert conn.execute("SELECT current_schema()").fetchone()[0].startswith("test_warehouse_")
        conn.execute(MIGRATION.read_text(encoding="utf-8-sig"))
    return s,lease,client


def send(lease):
    return recovery.record_pinned_send_boundary_without_dispatch(
        lease=lease,expected_revision=lease.revision)


def cancel(lease,revision):
    return cancellation.record_cancel_intent_without_provider(
        lease=lease,expected_revision=revision)


def test_original_job_cancel_intent_is_durable_and_idempotent(cancel_db):
    s,lease,client=cancel_db
    pin(s)
    sent=send(lease)
    first=cancel(lease,sent.revision)
    second=cancel(lease,sent.revision)
    assert first==second and first.job_id==lease.job.job_id
    assert not first.cancellation_confirmed and not first.settlement_authorized
    assert not first.provider_dispatched
    assert scalar(s,"SELECT count(*) FROM warehouse_query_cancel_intents")==1
    assert scalar(s,"SELECT count(*) FROM query_governance_usage")==0
    assert scalar(s,"SELECT status FROM query_governance_reservations WHERE reservation_id=%s",(s.r,))=="EXECUTING"
    assert scalar(s,"SELECT warehouse_query_publication_dependency_present(%s,%s)",(s.f.resource,s.pub['publication_id']))
    assert not client.calls


def test_pre_send_cancel_denied_and_never_contacts_provider(cancel_db):
    s,lease,client=cancel_db
    pin(s)
    with pytest.raises(repo.RecoveryConflict,match="CANCEL_REQUIRES_POSSIBLE_SEND"):
        cancel(lease,lease.revision)
    assert scalar(s,"SELECT count(*) FROM warehouse_query_cancel_intents")==0
    assert not client.calls


def test_stale_lease_is_rejected_before_cancel_request(cancel_db):
    s,lease,client=cancel_db
    pin(s)
    sent=send(lease)
    with pytest.raises(repo.RecoveryConflict):
        cancel(replace(lease,token=uuid4()),sent.revision)
    with pytest.raises(repo.RecoveryConflict):
        cancel(lease,lease.revision)
    assert scalar(s,"SELECT count(*) FROM warehouse_query_cancel_intents")==0
    assert not client.calls


def test_cancel_intent_cannot_be_updated_deleted_or_truncated(cancel_db):
    s,lease,client=cancel_db
    pin(s)
    sent=send(lease)
    cancel(lease,sent.revision)
    with s.f.pg["connect"]() as conn:
        for sql in (
            "UPDATE warehouse_query_cancel_intents SET provider_job_id='other'",
            "DELETE FROM warehouse_query_cancel_intents",
            "TRUNCATE warehouse_query_cancel_intents",
        ):
            with pytest.raises(psycopg.DatabaseError):
                with conn.transaction():
                    conn.execute(sql)
    assert scalar(s,"SELECT count(*) FROM warehouse_query_cancel_intents")==1
    assert not client.calls
