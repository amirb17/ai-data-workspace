"""Step 413E: recovery diagnostics preserve claimed PostgreSQL accounting."""
from pathlib import Path
import ast

import pytest

from app.query_governance.policy import MIB
from app.query_governance.providers.base import (
    QueryRecovery, QueryExecutionResult, RecoveryState,
)
from app.services.warehouse_sql_recovery_inspection_service import (
    WarehouseRecoveryInspectionState, inspect_claimed_warehouse_recovery,
)
from tests.test_query_governance_postgres import governance_pg
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_sql_admission_postgres import admit, service


class RecoveryOnlyProvider:
    identity = "BIGQUERY"

    def __init__(self, state, *, fail=False):
        self.state = state
        self.fail = fail
        self.calls = []

    def recover(self, query, permit):
        self.calls.append(("recover", query, permit))
        if self.fail:
            raise RuntimeError("synthetic transient recovery failure")
        job_id = f"dr_query_w{query.workspace_id}_r{permit.reservation_id}_{query.request_token[:32]}"
        result = None
        if self.state == RecoveryState.SUCCEEDED:
            result = QueryExecutionResult(
                provider_job_id=job_id, rows=(), truncated=False,
                bytes_processed=10*MIB, bytes_billed=10*MIB,
                payload_bytes=0, duration_ms=3,
            )
        return QueryRecovery(
            state=self.state, provider_job_id=job_id, result=result,
        )

    def execute(self, *a, **kw):
        pytest.fail("Recovery inspection must never execute a query")

    def estimate(self, *a, **kw):
        pytest.fail("Recovery inspection must never estimate a new query")


def claimed(fixture):
    booking = admit(fixture)
    svc = service(fixture)
    svc.claim(booking.reservation_id)
    return booking, svc


@pytest.mark.parametrize(("state","expected"),[
    (RecoveryState.NOT_FOUND, WarehouseRecoveryInspectionState.NOT_FOUND_HELD),
    (RecoveryState.IN_PROGRESS, WarehouseRecoveryInspectionState.IN_PROGRESS_HELD),
    (RecoveryState.SUCCEEDED, WarehouseRecoveryInspectionState.SUCCEEDED_UNSETTLED),
    (RecoveryState.FAILED, WarehouseRecoveryInspectionState.FAILED_UNSETTLED),
])
def test_each_recovery_state_remains_claimed_and_budgeted(governance_pg,state,expected):
    booking,svc=claimed(governance_pg)
    provider=RecoveryOnlyProvider(state)
    outcome=inspect_claimed_warehouse_recovery(
        governance=svc,query=booking.query,
        reservation_id=booking.reservation_id,provider=provider,
    )
    assert outcome.state == expected
    assert outcome.reservation_id == booking.reservation_id
    assert outcome.provider_job_id.endswith(booking.query.request_token[:32])
    assert not outcome.execution_authorized
    assert not outcome.reservation_released
    assert not outcome.accounting_finalized
    assert len(provider.calls)==1
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"
    assert svc.usage().reserved_bytes_today==10*MIB
    assert svc.usage().committed_bytes_today==0


def test_provider_error_never_releases_uncertain_claim(governance_pg):
    booking,svc=claimed(governance_pg)
    provider=RecoveryOnlyProvider(RecoveryState.NOT_FOUND,fail=True)
    with pytest.raises(RuntimeError,match="transient recovery failure"):
        inspect_claimed_warehouse_recovery(
            governance=svc,query=booking.query,
            reservation_id=booking.reservation_id,provider=provider,
        )
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"
    assert svc.usage().reserved_bytes_today==10*MIB


def test_active_unclaimed_reservation_never_queries_provider(governance_pg):
    booking=admit(governance_pg)
    provider=RecoveryOnlyProvider(RecoveryState.NOT_FOUND)
    with pytest.raises(ValueError,match="EXECUTING"):
        inspect_claimed_warehouse_recovery(
            governance=service(governance_pg),query=booking.query,
            reservation_id=booking.reservation_id,provider=provider,
        )
    assert provider.calls==[]


def test_request_token_mismatch_fails_before_provider_recovery(governance_pg):
    from dataclasses import replace
    booking,svc=claimed(governance_pg)
    provider=RecoveryOnlyProvider(RecoveryState.NOT_FOUND)
    changed=replace(booking.query,request_token="f"*64)
    with pytest.raises(ValueError,match="EXECUTING"):
        inspect_claimed_warehouse_recovery(
            governance=svc,query=changed,
            reservation_id=booking.reservation_id,provider=provider,
        )
    assert provider.calls==[]
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"


def test_wrong_owner_rejected_by_durable_repository(governance_pg):
    from app.db.query_governance_repository import ReservationConflict
    booking,svc=claimed(governance_pg)
    foreign=service(governance_pg,owner=governance_pg["other"])
    provider=RecoveryOnlyProvider(RecoveryState.NOT_FOUND)
    with pytest.raises(ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        inspect_claimed_warehouse_recovery(
            governance=foreign,query=booking.query,
            reservation_id=booking.reservation_id,provider=provider,
        )
    assert provider.calls==[]


def test_multiple_inspections_do_not_retry_execution(governance_pg):
    booking,svc=claimed(governance_pg)
    provider=RecoveryOnlyProvider(RecoveryState.IN_PROGRESS)
    for _ in range(2):
        observation=inspect_claimed_warehouse_recovery(
            governance=svc,query=booking.query,
            reservation_id=booking.reservation_id,provider=provider,
        )
        assert observation.state == WarehouseRecoveryInspectionState.IN_PROGRESS_HELD
    assert len(provider.calls)==2
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"
    assert svc.usage().reserved_bytes_today==10*MIB


def test_module_has_no_execute_claim_release_complete_or_provider_query():
    path=Path(__file__).parents[1]/"app/services/warehouse_sql_recovery_inspection_service.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    attrs=[node.attr for node in ast.walk(tree) if isinstance(node,ast.Attribute)]
    assert "recover" in attrs
    assert not set(attrs)&{"execute","claim","release","complete","admit","estimate","query"}
    modules=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(module and module.startswith(
        ("google","psycopg","app.query_governance.providers.bigquery")
    ) for module in modules)
