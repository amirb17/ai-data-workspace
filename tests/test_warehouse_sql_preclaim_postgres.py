"""Step 413C: guarded ACTIVE revalidation, release and recovery preservation."""
from dataclasses import replace
from pathlib import Path
import ast

import pytest

from app.query_governance.policy import MIB
from app.query_governance.types import QueryEstimate, EstimateConfidence
from app.services.warehouse_sql_compiled_evidence_service import WarehouseCompiledEvidence
from app.services.warehouse_sql_preclaim_service import (
    PreclaimState, revalidate_active_warehouse_reservation,
)
from tests.test_warehouse_sql_admission_postgres import admit, dry, service
from tests.test_warehouse_foundation import pg
from tests.test_query_governance_postgres import governance_pg


class DryOnlyProvider:
    identity="BIGQUERY"
    def __init__(self, *, estimate=None, fail=False):
        self.value=estimate if estimate is not None else QueryEstimate(10*MIB)
        self.fail=fail
        self.calls=[]
    def estimate(self, query):
        self.calls.append(query)
        if self.fail:
            raise RuntimeError("synthetic provider unavailable")
        return self.value
    def execute(self,*a,**k):
        pytest.fail("Warehouse preclaim must never execute a provider query")
    def recover(self,*a,**k):
        pytest.fail("Warehouse preclaim must never recover a provider query")


def verifier(booking,*,changed=False,fail=False):
    calls=[]
    def check():
        calls.append(True)
        if fail:
            raise ValueError("synthetic missing publication")
        template=replace(booking.query,request_token="f"*64)
        if changed and len(calls)==2:
            template=replace(template,sql=template.sql+" -- changed")
        return template,WarehouseCompiledEvidence(
            workspace_id=booking.query.workspace_id,
            model_count=len(booking.query.physical_tables),
        )
    check.calls=calls
    return check


def run(booking,fixture,*,provider=None,check=None):
    return revalidate_active_warehouse_reservation(
        admission=booking,governance=service(fixture),
        provider=provider if provider is not None else DryOnlyProvider(),
        verify_current=check if check is not None else verifier(booking),
    )


def test_valid_fresh_sql_and_cost_remain_active_and_non_authorizing(governance_pg):
    booking=admit(governance_pg)
    check=verifier(booking)
    provider=DryOnlyProvider()
    outcome=run(booking,governance_pg,provider=provider,check=check)
    assert outcome.state is PreclaimState.REVALIDATED_ACTIVE
    assert not outcome.execution_authorized
    assert not outcome.reservation_claimed
    assert len(provider.calls)==1
    assert len(check.calls)==2
    assert service(governance_pg).reservation(booking.reservation_id)["status"]=="ACTIVE"
    assert service(governance_pg).usage().reserved_bytes_today==10*MIB


@pytest.mark.parametrize("cost",[
    QueryEstimate(11*MIB),
    QueryEstimate(None,EstimateConfidence.UNKNOWN),
    QueryEstimate(9*MIB,EstimateConfidence.LOWER_BOUND),
    QueryEstimate(0),
])
def test_changed_or_unsafe_cost_releases_active_reservation(governance_pg,cost):
    booking=admit(governance_pg)
    outcome=run(booking,governance_pg,provider=DryOnlyProvider(estimate=cost))
    assert outcome.state is PreclaimState.RELEASED_REVALIDATION_REQUIRED
    assert service(governance_pg).reservation(booking.reservation_id)["status"]=="RELEASED"
    assert service(governance_pg).usage().reserved_bytes_today==0


def test_provider_failure_releases_unclaimed_budget(governance_pg):
    booking=admit(governance_pg)
    outcome=run(booking,governance_pg,provider=DryOnlyProvider(fail=True))
    assert outcome.state is PreclaimState.RELEASED_REVALIDATION_REQUIRED
    assert service(governance_pg).usage().workspace_active_queries==0


def test_changed_publication_after_dry_run_releases(governance_pg):
    booking=admit(governance_pg)
    check=verifier(booking,changed=True)
    outcome=run(booking,governance_pg,check=check)
    assert outcome.state is PreclaimState.RELEASED_REVALIDATION_REQUIRED
    assert len(check.calls)==2
    assert service(governance_pg).reservation(booking.reservation_id)["status"]=="RELEASED"


def test_missing_current_publication_releases_without_provider_io(governance_pg):
    booking=admit(governance_pg)
    provider=DryOnlyProvider()
    outcome=run(booking,governance_pg,provider=provider,
                check=verifier(booking,fail=True))
    assert outcome.state is PreclaimState.RELEASED_REVALIDATION_REQUIRED
    assert provider.calls==[]


def test_claimed_reservation_is_not_released_or_revalidated(governance_pg):
    booking=admit(governance_pg)
    svc=service(governance_pg)
    svc.claim(booking.reservation_id)
    provider=DryOnlyProvider()
    with pytest.raises(ValueError,match="CURRENT_ACTIVE"):
        run(booking,governance_pg,provider=provider)
    assert provider.calls==[]
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"
    assert svc.usage().reserved_bytes_today==10*MIB


def test_released_reservation_cannot_be_revalidated_again(governance_pg):
    booking=admit(governance_pg)
    svc=service(governance_pg)
    svc.release(booking.reservation_id)
    with pytest.raises(ValueError,match="CURRENT_ACTIVE"):
        run(booking,governance_pg)


def test_cross_workspace_governance_fails_before_database_read(governance_pg):
    booking=admit(governance_pg)
    altered=replace(booking,query=replace(
        booking.query,workspace_id=governance_pg["foreign_workspace"],
        physical_tables=tuple(name.replace("dr_dev_w1.","dr_dev_w2.")
                              for name in booking.query.physical_tables),
        sql=booking.query.sql.replace("dr_dev_w1.","dr_dev_w2."),
    ))
    with pytest.raises(ValueError,match="trusted verifier|ACTIVE candidate"):
        run(altered,governance_pg)


def test_preclaim_file_never_claims_executes_or_recovers():
    path=Path(__file__).parents[1]/"app/services/warehouse_sql_preclaim_service.py"
    node=ast.parse(path.read_text(encoding="utf-8"))
    attrs=[n.attr for n in ast.walk(node) if isinstance(n,ast.Attribute)]
    assert not set(attrs)&{"claim","execute","recover","complete","admit"}
    assert "release" in attrs
