"""414F3 attested warehouse billing through the existing governance ledger.

Only disposable PostgreSQL schemas and fake GET-only BigQuery metadata.
"""
import pytest

from app.services import warehouse_sql_attested_accounting_service as accounting
from app.services import warehouse_sql_terminal_evidence_service as evidence
from tests.test_warehouse_terminal_evidence_414f2 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    scalar, change, send_and_recover, inspect,
)


def settle(s):
    return accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)


def test_success_settled_exactly_once_and_keeps_publication_hold(pinned_db):
    s, lease, client = pinned_db
    send_and_recover(s, lease)
    first = settle(s)
    assert first.outcome == 'SUCCEEDED'
    assert first.accounted_bytes == 10485760
    assert first.accounting_basis == 'ACTUAL'
    assert not first.reused and not first.publication_release_authorized
    assert settle(s).reused
    assert scalar(s, "SELECT status FROM query_governance_reservations WHERE reservation_id=%s", (s.r,)) == 'COMPLETED'
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 1
    assert scalar(s, "SELECT warehouse_query_publication_dependency_present(%s,%s)",
                  (s.f.resource, s.pub['publication_id']))
    assert len(client.calls) == 1


@pytest.mark.parametrize("reason", ['invalidQuery', 'stopped'])
def test_terminal_failure_and_cancel_account_exact_bytes_once(pinned_db, reason):
    s, lease, client = pinned_db
    client.body['status']['errorResult'] = {'reason': reason}
    send_and_recover(s, lease)
    first = settle(s)
    assert first.outcome == 'FAILED'
    assert first.accounting_basis == 'ACTUAL'
    assert first.accounted_bytes == 10485760
    assert not first.reused and settle(s).reused
    assert scalar(s, "SELECT status FROM query_governance_reservations WHERE reservation_id=%s", (s.r,)) == 'FAILED'
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 1
    assert scalar(s, "SELECT warehouse_query_publication_dependency_present(%s,%s)",
                  (s.f.resource, s.pub['publication_id']))


def test_missing_terminal_report_fails_closed_without_changing_reservation(pinned_db):
    s, lease, client = pinned_db
    from tests.test_warehouse_query_envelope_pinning_414f1 import pin
    from app.services import warehouse_sql_send_recovery_service as recovery
    pin(s)
    recovery.record_pinned_send_boundary_without_dispatch(lease=lease, expected_revision=lease.revision)
    with pytest.raises(accounting.WarehouseAccountingConflict, match="VERIFIED_BILLING"):
        settle(s)
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 0
    assert scalar(s, "SELECT status FROM query_governance_reservations WHERE reservation_id=%s", (s.r,)) == 'EXECUTING'
    assert not client.calls


def test_incomplete_failure_billing_cannot_be_settled(pinned_db):
    s, lease, client = pinned_db
    client.body['status']['errorResult']={'reason':'invalidQuery'}
    client.body['statistics']['query'].pop('totalBytesBilled')
    send_and_recover(s,lease)
    with pytest.raises(accounting.WarehouseAccountingConflict,match="VERIFIED_BILLING"):
        settle(s)
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage") == 0


def test_accounting_failure_rolls_back_usage_and_reservation(pinned_db,monkeypatch):
    s, lease, client = pinned_db
    send_and_recover(s,lease)
    real = accounting.accounting.complete
    def fail_after_insert(conn,**kwargs):
        real(conn,**kwargs)
        raise RuntimeError('Simulated failure before transaction commit')
    with monkeypatch.context() as patch:
        patch.setattr(accounting.accounting,'complete',fail_after_insert)
        with pytest.raises(RuntimeError,match="before transaction commit"):
            settle(s)
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage") == 0
    assert scalar(s,"SELECT status FROM query_governance_reservations WHERE reservation_id=%s",(s.r,)) == 'EXECUTING'
    assert settle(s).outcome == 'SUCCEEDED'
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage") == 1
