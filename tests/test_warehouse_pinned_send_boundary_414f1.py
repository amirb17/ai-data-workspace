"""414F1: pinned envelope -> atomic PostgreSQL send boundary, without cloud IO.

Tests use the real isolated PostgreSQL journal + migrated envelope guard.
"""
from dataclasses import replace
from uuid import uuid4

import pytest

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_send_recovery_service as recovery
from tests.test_warehouse_query_envelope_pinning_414f1 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    pin, scalar, change,
)


def boundary(lease, revision=None):
    return recovery.record_pinned_send_boundary_without_dispatch(
        lease=lease,
        expected_revision=lease.revision if revision is None else revision,
    )


def test_exact_pinned_request_commits_once_and_requires_lookup(pinned_db):
    s, lease, client = pinned_db
    saved = pin(s)
    before = scalar(s, "SELECT count(*) FROM warehouse_query_send_journal")
    result = boundary(lease)
    assert result.phase == "SEND_UNKNOWN"
    assert result.classification == "LOOKUP_REQUIRED"
    assert result.job.job_id == saved.job_id
    assert not result.execution_authorized and not result.provider_dispatched
    assert scalar(s, "SELECT count(*) FROM warehouse_query_send_journal") == before + 1
    assert scalar(s, "SELECT count(*) FROM warehouse_query_request_envelopes") == 1
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0
    assert not client.calls


def test_missing_pinned_request_never_advances_journal(pinned_db):
    s, lease, client = pinned_db
    with pytest.raises(repo.RecoveryConflict, match="EXACT_PINNED_REQUEST_REQUIRED"):
        boundary(lease)
    assert recovery.inspect_recovery(attempt_id=s.a.attempt_id).phase == "INTENT_RECORDED"
    assert scalar(s, "SELECT count(*) FROM warehouse_query_request_envelopes") == 0
    assert not client.calls


def test_second_boundary_and_reclaimed_worker_cannot_resend(pinned_db):
    s, lease, client = pinned_db
    pin(s)
    result = boundary(lease)
    with pytest.raises(repo.RecoveryConflict):
        boundary(lease)
    newer = recovery.acquire_recovery(
        attempt_id=s.a.attempt_id, expected_revision=result.revision,
    )
    assert newer.job == result.job
    with pytest.raises(repo.RecoveryConflict, match="BOUNDARY_ALREADY_RECORDED"):
        boundary(newer)
    assert scalar(
        s, "SELECT count(*) FROM warehouse_query_send_journal WHERE event='SEND_BOUNDARY'"
    ) == 1
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0
    assert not client.calls


def test_revoked_checkpoint_does_not_cross_pinned_boundary(pinned_db):
    s, lease, client = pinned_db
    pin(s)
    change(s, "account")
    with pytest.raises(repo.RecoveryConflict):
        boundary(lease)
    assert scalar(
        s, "SELECT count(*) FROM warehouse_query_send_journal WHERE event='SEND_BOUNDARY'"
    ) == 0
    assert scalar(s, "SELECT count(*) FROM warehouse_query_request_envelopes") == 1
    assert not client.calls


def test_stale_recovery_lease_has_no_effect(pinned_db):
    s, lease, client = pinned_db
    pin(s)
    with pytest.raises(repo.RecoveryConflict):
        boundary(replace(lease, token=uuid4()))
    with pytest.raises(repo.RecoveryConflict):
        boundary(lease, lease.revision - 1)
    assert scalar(
        s, "SELECT count(*) FROM warehouse_query_send_journal WHERE event='SEND_BOUNDARY'"
    ) == 0
    assert not client.calls
