"""414F2 actual PostgreSQL terminal attestation evidence gate, fake jobs.get."""
from dataclasses import replace

import pytest

from app.services import warehouse_sql_terminal_evidence_service as terminal
from app.services import warehouse_sql_provider_policy_service as policy
from app.services import warehouse_sql_send_recovery_service as recovery
from tests.test_warehouse_query_envelope_pinning_414f1 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    pin, scalar, change,
)


def send_and_recover(s, lease):
    pin(s)
    sent = recovery.record_pinned_send_boundary_without_dispatch(
        lease=lease, expected_revision=lease.revision,
    )
    return policy.lookup_pinned_submission_without_resend(
        lease=lease, expected_revision=sent.revision,
    )


def inspect(s):
    return terminal.inspect_terminal_evidence(attempt_id=s.a.attempt_id)


def test_verified_succeeded_evidence_preserves_accounting_hold(pinned_db):
    s, lease, client = pinned_db
    result = send_and_recover(s, lease)
    assert result.verdict == "VERIFIED_METADATA" and result.observation == "SUCCEEDED"
    evidence = inspect(s)
    assert evidence.classification == "VERIFIED_SUCCESS_REQUIRES_SETTLEMENT"
    assert evidence.bytes_processed == 1024 and evidence.bytes_billed == 10485760
    assert evidence.provider_job_id == lease.job.job_id
    assert not evidence.settlement_authorized and not evidence.publication_release_authorized
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 0
    assert scalar(s, "SELECT COUNT(*) FROM warehouse_query_send_journal WHERE event='SEND_BOUNDARY'") == 1
    assert len(client.calls) == 1


@pytest.mark.parametrize("reason,observation", [
    ("invalidQuery", "FAILED"), ("stopped", "CANCELLED"),
])
def test_failed_or_cancelled_complete_metadata_is_not_immediately_settled(pinned_db, reason, observation):
    s, lease, client = pinned_db
    client.body["status"]["errorResult"] = {"reason": reason}
    result = send_and_recover(s, lease)
    assert result.verdict == "VERIFIED_METADATA" and result.observation == observation
    evidence = inspect(s)
    assert evidence.classification == "VERIFIED_NON_SUCCESS_REQUIRES_SETTLEMENT"
    assert evidence.observation == observation
    assert not evidence.settlement_authorized
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 0


def test_failed_incomplete_billing_keeps_reservation_held(pinned_db):
    s, lease, client = pinned_db
    client.body["status"]["errorResult"] = {"reason": "invalidQuery"}
    client.body["statistics"]["query"].pop("totalBytesBilled")
    result = send_and_recover(s, lease)
    assert result.verdict == "VERIFIED_METADATA" and result.observation == "FAILED"
    evidence = inspect(s)
    assert evidence.classification == "BILLING_EVIDENCE_INCOMPLETE_HELD"
    assert evidence.bytes_processed is None and evidence.bytes_billed is None
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 0


def test_no_attested_terminal_evidence_keeps_accounting_held(pinned_db):
    s, lease, client = pinned_db
    pin(s)
    recovery.record_pinned_send_boundary_without_dispatch(
        lease=lease, expected_revision=lease.revision,
    )
    evidence = inspect(s)
    assert evidence.classification == "TERMINAL_EVIDENCE_INCOMPLETE_HELD"
    assert not evidence.settlement_authorized
    assert not client.calls


def test_terminal_evidence_rejects_attempt_without_original_pin(pinned_db):
    s, lease, client = pinned_db
    with pytest.raises(terminal.TerminalEvidenceConflict, match="PINNED"):
        inspect(s)
    assert not client.calls
