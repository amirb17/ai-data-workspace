"""414F4: one-way cancel boundary -> original GET recovery and settlement.

All calls use disposable PostgreSQL + fake jobs.get. No BigQuery cancel.
"""
import pytest
from google.api_core.exceptions import NotFound

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_cancel_attempt_boundary_service as fence
from app.services import warehouse_sql_send_recovery_service as recovery
from app.services import warehouse_sql_attested_accounting_service as accounting
from tests.test_warehouse_cancel_attempt_boundary_414f4 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query, served,
    checkpointed, prepared, lookup, unbound, pinned_db, cancel_db, fenced_db,
    pin, scalar, send, cancel, prepare, boundary, count,
)
from tests.test_warehouse_sql_provider_policy_414e3 import wait_lease


def recover(lease, revision):
    return fence.lookup_after_cancel_boundary_without_resend(
        lease=lease, expected_revision=revision,
    )


def test_cancelled_job_verification_then_exactly_once_accounting(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    boundary(lease, revision)
    client.body["status"]["errorResult"] = {"reason": "stopped"}
    found = recover(lease, revision)
    assert found.verdict == "VERIFIED_METADATA" and found.observation == "CANCELLED"
    assert found.recovery.job.job_id == lease.job.job_id
    assert not found.execution_authorized and not found.provider_dispatched
    settled = accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    assert settled.outcome == "FAILED" and not settled.reused
    assert accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id).reused
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 1
    assert scalar(s, "SELECT warehouse_query_publication_dependency_present(%s,%s)",
                  (s.f.resource,s.pub["publication_id"]))
    assert count(s) == 1 and len(client.calls) == 1


def test_job_can_succeed_after_cancel_and_is_accounted_as_success(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    boundary(lease, revision)
    found = recover(lease, revision)
    assert found.observation == "SUCCEEDED"
    accounted = accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    assert accounted.outcome == "SUCCEEDED"
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 1
    assert count(s) == 1 and len(client.calls) == 1


def test_not_found_after_cancel_retains_hold_and_no_accounting(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    boundary(lease, revision)
    client.error = NotFound("might still be running")
    absent = recover(lease, revision)
    assert absent.observation == "NOT_FOUND" and absent.recovery.phase == "SEND_UNKNOWN"
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 0
    assert scalar(s, "SELECT status FROM query_governance_reservations WHERE reservation_id=%s", (s.r,)) == "EXECUTING"
    assert count(s) == 1 and len(client.calls) == 1


def test_recovery_without_cancel_boundary_fails_before_provider_get(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    with pytest.raises(repo.RecoveryConflict, match="IMMUTABLE_CANCEL_BOUNDARY"):
        recover(lease, revision)
    assert not client.calls and count(s) == 0


@pytest.mark.parametrize("lookup", [1], indirect=True)
def test_expired_worker_takeover_does_get_only_not_another_cancel(fenced_db):
    s, lease, client = fenced_db
    revision = prepare(s, lease)
    boundary(lease, revision)
    wait_lease(s,lease)
    current = recovery.inspect_recovery(attempt_id=s.a.attempt_id)
    replacement = recovery.acquire_recovery(
        attempt_id=s.a.attempt_id, expected_revision=current.revision,
    )
    assert replacement.job == lease.job
    with pytest.raises(repo.RecoveryConflict):
        boundary(replacement,replacement.revision)
    with pytest.raises(repo.RecoveryConflict):
        recover(lease,revision)
    result = recover(replacement,replacement.revision)
    assert result.verdict == "VERIFIED_METADATA"
    assert result.recovery.job == lease.job
    assert count(s) == 1 and len(client.calls) == 1
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 0
