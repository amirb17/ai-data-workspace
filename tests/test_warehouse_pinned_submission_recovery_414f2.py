"""414F2: PostgreSQL pinned send boundary -> GET-only attested recovery."""
from dataclasses import replace
from uuid import uuid4

from google.api_core.exceptions import NotFound
import pytest

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_provider_policy_service as policy
from app.services import warehouse_sql_send_recovery_service as recovery
from tests.test_warehouse_query_envelope_pinning_414f1 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    pin, scalar, change,
)


def recover(lease, revision=None):
    return policy.lookup_pinned_submission_without_resend(
        lease=lease, expected_revision=lease.revision if revision is None else revision,
    )


def send_boundary(lease):
    return recovery.record_pinned_send_boundary_without_dispatch(
        lease=lease, expected_revision=lease.revision,
    )


def test_pinned_submitted_job_is_verified_by_get_only_without_settlement(pinned_db):
    s, lease, client = pinned_db
    saved = pin(s)
    boundary = send_boundary(lease)
    checked = recover(lease, boundary.revision)
    assert checked.verdict == "VERIFIED_METADATA"
    assert checked.observation == "SUCCEEDED"
    assert checked.recovery.job.job_id == saved.job_id
    assert checked.recovery.classification == "TERMINAL_OBSERVED_HELD"
    assert not checked.execution_authorized and not checked.provider_dispatched
    assert len(client.calls) == 1
    assert scalar(s, "SELECT count(*) FROM warehouse_query_recovery_attestations") == 1
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0
    assert scalar(s, "SELECT warehouse_query_publication_dependency_present(%s,%s)",
                  (s.f.resource, s.pub["publication_id"]))


def test_missing_boundary_rejected_before_any_provider_lookup(pinned_db):
    s, lease, client = pinned_db
    pin(s)
    with pytest.raises(repo.RecoveryConflict, match="SEND_BOUNDARY_REQUIRED"):
        recover(lease)
    assert not client.calls
    assert scalar(s, "SELECT count(*) FROM warehouse_query_recovery_attestations") == 0


def test_unknown_get_then_same_identity_is_found_without_resend(pinned_db):
    s, lease, client = pinned_db
    saved = pin(s)
    boundary = send_boundary(lease)
    client.error = NotFound("ambiguous absence")
    missing = recover(lease, boundary.revision)
    assert missing.observation == "NOT_FOUND" and missing.verdict == "ABSENT"
    assert missing.recovery.phase == "SEND_UNKNOWN"
    client.error = None
    found = recover(lease, missing.revision)
    assert found.verdict == "VERIFIED_METADATA"
    assert found.recovery.job.job_id == saved.job_id
    assert len(client.calls) == 2
    assert scalar(s, "SELECT count(*) FROM warehouse_query_recovery_attestations") == 2
    assert scalar(s, "SELECT count(*) FROM warehouse_query_send_journal WHERE event='SEND_BOUNDARY'") == 1
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0


def test_stale_recovery_revision_cannot_lookup_after_valid_observation(pinned_db):
    s, lease, client = pinned_db
    pin(s)
    boundary = send_boundary(lease)
    checked = recover(lease, boundary.revision)
    with pytest.raises(repo.RecoveryConflict):
        recover(lease, boundary.revision)
    with pytest.raises(repo.RecoveryConflict):
        recover(replace(lease, token=uuid4()), checked.revision)
    assert len(client.calls) == 1
    assert scalar(s, "SELECT count(*) FROM warehouse_query_recovery_attestations") == 1


def test_account_revocation_after_possible_send_still_allows_same_job_lookup(pinned_db):
    s, lease, client = pinned_db
    saved = pin(s)
    boundary = send_boundary(lease)
    change(s, "account")
    result = recover(lease, boundary.revision)
    assert result.verdict == "VERIFIED_METADATA"
    assert result.recovery.revoked is True
    assert result.recovery.job.job_id == saved.job_id
    assert result.recovery.classification == "REVOKED_HELD"
    assert len(client.calls) == 1
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0
