"""Step 413F: durable recovery settlement with real isolated PostgreSQL."""
import ast
from dataclasses import replace
from pathlib import Path

import pytest

from app.db.query_governance_repository import ReservationConflict
from app.query_governance.policy import MIB
from app.query_governance.providers.base import (
    QueryRecovery, RecoveryState, QueryExecutionResult,
)
from app.services.governed_query_service import QueryRecoveryRequired
from app.services.warehouse_sql_recovery_settlement_service import (
    WarehouseSettlementState, settle_claimed_warehouse_query,
)
from tests.test_query_governance_postgres import governance_pg
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_sql_admission_postgres import admit, service


class RecoveryProvider:
    identity="BIGQUERY"

    def __init__(self, state, *, fail_lookup=False):
        self.state=state
        self.fail_lookup=fail_lookup
        self.calls=[]

    def recover(self,query,permit):
        self.calls.append(("recover",query,permit))
        if self.fail_lookup:
            raise RuntimeError("synthetic provider lookup failure")
        job_id=(f"dr_query_w{query.workspace_id}_r{permit.reservation_id}_"
                f"{query.request_token[:32]}")
        if self.state==RecoveryState.SUCCEEDED:
            result=QueryExecutionResult(
                provider_job_id=job_id,rows=(),truncated=False,
                bytes_processed=7*MIB,bytes_billed=10*MIB,
                payload_bytes=0,duration_ms=12,
            )
            return QueryRecovery(
                state=self.state,provider_job_id=job_id,result=result,
            )
        if self.state==RecoveryState.FAILED:
            return QueryRecovery(
                state=self.state,provider_job_id=job_id,
                bytes_processed=None,bytes_billed=None,duration_ms=8,
            )
        return QueryRecovery(state=self.state,provider_job_id=job_id)

    def execute(self,*args,**kwargs):
        pytest.fail("Warehouse recovery settlement must never execute SQL")

    def estimate(self,*args,**kwargs):
        pytest.fail("Warehouse recovery settlement must never start dry run")


def claimed(fixture):
    booking=admit(fixture)
    svc=service(fixture)
    svc.claim(booking.reservation_id)
    return booking,svc


def settle(booking,svc,provider):
    return settle_claimed_warehouse_query(
        governance=svc,query=booking.query,
        reservation_id=booking.reservation_id,provider=provider,
    )


def test_success_settles_once_with_actual_usage(governance_pg):
    booking,svc=claimed(governance_pg)
    provider=RecoveryProvider(RecoveryState.SUCCEEDED)
    outcome=settle(booking,svc,provider)
    assert outcome.state==WarehouseSettlementState.SUCCEEDED
    assert outcome.accounted_bytes==10*MIB
    assert not outcome.execution_authorized
    assert not outcome.provider_dispatched
    assert [call[0] for call in provider.calls]==["recover"]
    assert svc.reservation(booking.reservation_id)["status"]=="COMPLETED"
    usage=svc.usage()
    assert usage.reserved_bytes_today==0
    assert usage.committed_bytes_today==10*MIB
    with governance_pg["connect"]() as conn:
        count=conn.execute(
            "SELECT count(*) FROM query_governance_usage WHERE reservation_id=%s",
            (booking.reservation_id,),
        ).fetchone()[0]
        assert count==1


def test_confirmed_failed_job_uses_conservative_fallback(governance_pg):
    booking,svc=claimed(governance_pg)
    outcome=settle(booking,svc,RecoveryProvider(RecoveryState.FAILED))
    assert outcome.state==WarehouseSettlementState.FAILED
    assert not outcome.execution_authorized
    assert svc.reservation(booking.reservation_id)["status"]=="FAILED"
    usage=svc.usage()
    assert usage.reserved_bytes_today==0
    assert usage.committed_bytes_today==10*MIB
    with governance_pg["connect"]() as conn:
        rows=conn.execute(
            "SELECT outcome,accounting_basis,accounted_bytes FROM "
            "query_governance_usage WHERE reservation_id=%s",
            (booking.reservation_id,),
        ).fetchall()
        assert rows==[("FAILED","RESERVED_FALLBACK",10*MIB)]


@pytest.mark.parametrize("state",[RecoveryState.NOT_FOUND,RecoveryState.IN_PROGRESS])
def test_missing_or_running_job_keeps_reservation_and_budget(governance_pg,state):
    booking,svc=claimed(governance_pg)
    provider=RecoveryProvider(state)
    with pytest.raises(QueryRecoveryRequired) as caught:
        settle(booking,svc,provider)
    assert caught.value.reservation_id==booking.reservation_id
    assert len(provider.calls)==1
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"
    assert svc.usage().reserved_bytes_today==10*MIB
    assert svc.usage().committed_bytes_today==0


def test_uncertain_provider_error_preserves_claim(governance_pg):
    booking,svc=claimed(governance_pg)
    with pytest.raises(QueryRecoveryRequired):
        settle(booking,svc,RecoveryProvider(RecoveryState.SUCCEEDED,fail_lookup=True))
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"
    assert svc.usage().reserved_bytes_today==10*MIB


@pytest.mark.parametrize("state",[RecoveryState.SUCCEEDED,RecoveryState.FAILED])
def test_terminal_replay_cannot_settle_again_or_execute(governance_pg,state):
    booking,svc=claimed(governance_pg)
    provider=RecoveryProvider(state)
    settle(booking,svc,provider)
    with pytest.raises(ValueError,match="Executing durable reservation required"):
        settle(booking,svc,provider)
    assert len(provider.calls)==1
    with governance_pg["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM query_governance_usage WHERE reservation_id=%s",
            (booking.reservation_id,),
        ).fetchone()[0]==1


def test_active_reservation_cannot_settle(governance_pg):
    booking=admit(governance_pg)
    provider=RecoveryProvider(RecoveryState.SUCCEEDED)
    with pytest.raises(ValueError,match="Executing durable reservation required"):
        settle(booking,service(governance_pg),provider)
    assert provider.calls==[]


def test_wrong_request_identity_fails_before_job_lookup(governance_pg):
    booking,svc=claimed(governance_pg)
    provider=RecoveryProvider(RecoveryState.SUCCEEDED)
    wrong=replace(booking,query=replace(booking.query,request_token="f"*64))
    with pytest.raises(ValueError,match="SCOPE_MISMATCH"):
        settle(wrong,svc,provider)
    assert provider.calls==[]
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"


def test_foreign_owner_cannot_settle(governance_pg):
    booking,_=claimed(governance_pg)
    provider=RecoveryProvider(RecoveryState.SUCCEEDED)
    foreign=service(governance_pg,owner=governance_pg["other"])
    with pytest.raises(ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        settle(booking,foreign,provider)
    assert provider.calls==[]


def test_settlement_service_never_calls_dispatch_or_reservation_release():
    path=(Path(__file__).parents[1]/
          "app/services/warehouse_sql_recovery_settlement_service.py")
    root=ast.parse(path.read_text(encoding="utf-8"))
    attrs=[n.attr for n in ast.walk(root) if isinstance(n,ast.Attribute)]
    assert not set(attrs)&{"execute","claim","release","admit","estimate","query"}
    assert "reservation" in attrs
    imported=[n.module for n in ast.walk(root) if isinstance(n,ast.ImportFrom)]
    assert not any(n and n.startswith(("google","psycopg"))
                   for n in imported)
