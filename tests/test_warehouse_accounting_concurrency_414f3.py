"""414F3 concurrent settlement and replay consistency, disposable PG only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.services import warehouse_sql_attested_accounting_service as accounting
from tests.test_warehouse_terminal_evidence_414f2 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    scalar, send_and_recover,
)


def _race(s):
    barrier = Barrier(2)
    def worker():
        barrier.wait(timeout=10)
        return accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    return results


@pytest.mark.parametrize("failure", [False, True])
def test_two_workers_settle_one_usage_row_and_same_provider_identity(pinned_db, failure):
    s, lease, client = pinned_db
    if failure:
        client.body["status"]["errorResult"] = {"reason": "invalidQuery"}
    send_and_recover(s, lease)
    first, second = _race(s)
    assert sorted([first.reused, second.reused]) == [False, True]
    assert first.provider_job_id == second.provider_job_id == lease.job.job_id
    assert first.accounted_bytes == second.accounted_bytes == 10485760
    expected = "FAILED" if failure else "SUCCEEDED"
    assert first.outcome == second.outcome == expected
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 1
    assert scalar(s, "SELECT COUNT(*) FROM warehouse_query_send_journal WHERE event='SEND_BOUNDARY'") == 1
    assert scalar(s, "SELECT warehouse_query_publication_dependency_present(%s,%s)",
                  (s.f.resource, s.pub["publication_id"]))
    assert len(client.calls) == 1


@pytest.mark.parametrize("field,bad", [
    ("accounted_bytes", 0),
    ("provider_job_id", "forged"),
    ("duration_ms", 500),
    ("accounting_basis", "RESERVED_FALLBACK"),
])
def test_settled_replay_refuses_conflicting_accounting_evidence(pinned_db, monkeypatch, field, bad):
    s, lease, client = pinned_db
    send_and_recover(s, lease)
    normal = accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    assert not normal.reused
    read = accounting.repo.one

    def simulated_corrupt_read(conn, sql, params=()):
        row = read(conn, sql, params)
        if "SELECT * FROM query_governance_usage WHERE reservation_id" in sql and row is not None:
            return {**row, field: bad}
        return row

    with monkeypatch.context() as patch:
        patch.setattr(accounting.repo, "one", simulated_corrupt_read)
        with pytest.raises(accounting.WarehouseAccountingConflict, match="SETTLED_WAREHOUSE_EVIDENCE_CONFLICT"):
            accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    assert accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id).reused
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 1
    assert len(client.calls) == 1
