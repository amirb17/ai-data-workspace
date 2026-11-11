"""414F5 result read eligibility: disposable PG and fake GET-only provider."""
import pytest
from app.services import warehouse_sql_result_eligibility_service as results
from app.services import warehouse_sql_attested_accounting_service as accounting
from tests.test_warehouse_terminal_evidence_414f2 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query, served,
    checkpointed, prepared, lookup, unbound, pinned_db, scalar, send_and_recover,
)


def eligibility(s):
    return results.inspect_settled_result_eligibility(attempt_id=s.a.attempt_id)


def test_succeeded_and_settled_query_gives_bounded_read_eligibility(pinned_db):
    s, lease, client = pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    result=eligibility(s)
    assert result.job_id==lease.job.job_id
    assert result.project==lease.job.project
    assert result.location==lease.job.location
    assert 1 <= result.max_page_rows <= 100
    assert not result.provider_dispatched and not result.result_fetched
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage")==1
    assert len(client.calls)==1


def test_unsettled_success_cannot_fetch_rows(pinned_db):
    s, lease, client = pinned_db
    send_and_recover(s,lease)
    with pytest.raises(results.ResultEligibilityConflict,match="SETTLED_SUCCESS_REQUIRED"):
        eligibility(s)
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage")==0


@pytest.mark.parametrize("reason",["invalidQuery","stopped"])
def test_failed_or_cancelled_settlement_can_never_fetch_rows(pinned_db,reason):
    s, lease, client = pinned_db
    client.body["status"]["errorResult"]={"reason":reason}
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    with pytest.raises(results.ResultEligibilityConflict,match="SETTLED_SUCCESS_REQUIRED"):
        eligibility(s)
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage")==1


def test_unknown_job_never_qualifies_for_results(pinned_db):
    s, lease, client = pinned_db
    from tests.test_warehouse_query_envelope_pinning_414f1 import pin
    from app.services import warehouse_sql_send_recovery_service as recovery
    pin(s)
    recovery.record_pinned_send_boundary_without_dispatch(lease=lease,expected_revision=lease.revision)
    with pytest.raises(results.ResultEligibilityConflict):
        eligibility(s)
    assert not client.calls
