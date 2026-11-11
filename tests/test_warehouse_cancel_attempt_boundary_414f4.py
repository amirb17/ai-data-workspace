"""414F4 one-shot cancel boundary; real disposable PostgreSQL, zero cloud IO."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_cancel_attempt_boundary_service as fence
from app.services import warehouse_sql_send_recovery_service as recovery
from tests.test_warehouse_cancel_intent_414f4 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db, cancel_db,
    pin, scalar, send, cancel,
)
from tests.test_warehouse_sql_provider_policy_414e3 import wait_lease

MIGRATION = Path(__file__).resolve().parents[1] / (
    'migrations/0060_warehouse_query_cancel_attempt_boundary.sql'
)

@pytest.fixture
def fenced_db(cancel_db):
    s, lease, client = cancel_db
    with s.f.pg['connect']() as conn:
        assert conn.execute('SELECT current_schema()').fetchone()[0].startswith('test_warehouse_')
        conn.execute(MIGRATION.read_text(encoding='utf-8-sig'))
    return s, lease, client

def prepare(s, lease):
    pin(s)
    sent = send(lease)
    cancel(lease, sent.revision)
    return sent.revision

def boundary(lease, revision):
    return fence.record_cancel_attempt_boundary_without_provider(
        lease=lease, expected_revision=revision,
    )

def count(s):
    return scalar(s, 'SELECT count(*) FROM warehouse_query_cancel_attempt_boundaries')

def test_one_committed_marker_requires_original_identity(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    result = boundary(lease, revision)
    assert result.job_id == lease.job.job_id
    assert result.classification == 'CANCEL_POSSIBLY_SENT_LOOKUP_REQUIRED'
    assert not result.cancellation_confirmed and not result.settlement_authorized
    assert not result.provider_dispatched
    assert count(s) == 1
    assert scalar(s, 'SELECT count(*) FROM query_governance_usage') == 0
    assert scalar(s, 'SELECT warehouse_query_publication_dependency_present(%s,%s)',
                  (s.f.resource,s.pub['publication_id']))
    assert not client.calls

def test_second_attempt_fails_permanently_without_contacting_provider(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    boundary(lease, revision)
    with pytest.raises(repo.RecoveryConflict, match='CANCEL_ALREADY_POSSIBLY_SENT'):
        boundary(lease, revision)
    assert count(s) == 1 and not client.calls

def test_missing_cancel_intent_rejected(fenced_db):
    s, lease, client = fenced_db
    pin(s)
    sent = send(lease)
    with pytest.raises(repo.RecoveryConflict, match='EXACT_CANCEL_JOB_AND_INTENT_REQUIRED'):
        boundary(lease,sent.revision)
    assert count(s) == 0 and not client.calls

def test_stale_lease_and_revision_rejected(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    with pytest.raises(repo.RecoveryConflict):
        boundary(replace(lease,token=uuid4()),revision)
    with pytest.raises(repo.RecoveryConflict):
        boundary(lease,revision-1)
    assert count(s) == 0 and not client.calls

def test_concurrent_workers_commit_only_one_cancel_marker(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    gate = Barrier(2)
    def worker(_):
        gate.wait(timeout=10)
        try:
            return boundary(lease, revision)
        except repo.RecoveryConflict as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker,range(2)))
    assert sum(isinstance(x,fence.CancelBoundary) for x in results) == 1
    assert sum(isinstance(x,str) and 'CANCEL_ALREADY_POSSIBLY_SENT' in x for x in results) == 1
    assert count(s) == 1 and not client.calls

def test_marker_is_immutable_even_after_crash_or_takeover(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    boundary(lease, revision)
    with s.f.pg['connect']() as conn:
        for stmt in (
            "DELETE FROM warehouse_query_cancel_attempt_boundaries",
            "UPDATE warehouse_query_cancel_attempt_boundaries SET provider_job_id='other'",
            "TRUNCATE warehouse_query_cancel_attempt_boundaries",
        ):
            with pytest.raises(psycopg.DatabaseError):
                with conn.transaction():
                    conn.execute(stmt)
    assert count(s) == 1 and not client.calls
