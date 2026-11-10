"""Step 413D: real PostgreSQL claim rehearsal, zero provider dispatch."""
import ast
from dataclasses import replace
from pathlib import Path

import pytest

from app.db import query_governance_repository as repository
from app.query_governance.policy import MIB
from app.services.warehouse_sql_claim_rehearsal_service import (
    WarehouseClaimRehearsalState, WarehouseClaimRecoveryRequired,
    inspect_claim_transition_without_dispatch,
)
from app.services.warehouse_sql_compiled_evidence_service import WarehouseCompiledEvidence
from app.services.warehouse_sql_preclaim_service import (
    PreclaimState, WarehousePreclaimResult,
)
from tests.test_query_governance_postgres import governance_pg
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_sql_admission_postgres import admit, service
from tests.test_warehouse_sql_single_model_compiler import input_pair


def context(fixture):
    booking=admit(fixture)
    preclaim=WarehousePreclaimResult(
        state=PreclaimState.REVALIDATED_ACTIVE,
        reservation_id=booking.reservation_id,
    )
    _,original_binding=input_pair()
    original_model=original_binding.models[0]
    physical=original_model.physical_table.replace(
        "dr_dev_w7.",f"dr_dev_w{fixture['workspace']}.")
    bound=replace(original_binding,models=(
        replace(original_model,physical_table=physical),))
    template=replace(booking.query,request_token="f"*64)
    evidence=WarehouseCompiledEvidence(
        workspace_id=fixture["workspace"],
        model_count=len(bound.models),
    )
    return booking,preclaim,bound,template,evidence


def rehearse(fixture,booking,preclaim,bound,template,evidence,*,resolver=None):
    return inspect_claim_transition_without_dispatch(
        governance=service(fixture),admission=booking,preclaim=preclaim,
        resolve_current=(lambda:(bound,template,evidence))
            if resolver is None else resolver,
    )


def test_claimed_but_not_executed_and_budget_remains_held(governance_pg):
    booking,preclaim,bound,template,evidence=context(governance_pg)
    result=rehearse(governance_pg,booking,preclaim,bound,template,evidence)
    assert result.state==WarehouseClaimRehearsalState.CLAIMED_NO_EXECUTION
    assert not result.execution_authorized
    assert not result.provider_dispatched
    svc=service(governance_pg)
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"
    assert svc.usage().reserved_bytes_today==10*MIB
    with pytest.raises(repository.ReservationConflict,match="RESERVATION_NOT_ACTIVE"):
        svc.release(booking.reservation_id)


def test_publication_changes_before_claim_no_state_transition(governance_pg):
    booking,preclaim,bound,template,evidence=context(governance_pg)
    changed=replace(bound,models=(
        replace(bound.models[0],publication_revision=9),))
    with pytest.raises(ValueError,match="publication"):
        rehearse(governance_pg,booking,preclaim,bound,template,evidence,
                 resolver=lambda:(changed,template,evidence))
    assert service(governance_pg).reservation(booking.reservation_id)["status"]=="ACTIVE"


@pytest.mark.parametrize(("pin","changed_value"),[
    ("model_id",99),
    ("model_version_id",99),
    ("generation",99),
    ("publication_id",99),
    ("publication_revision",99),
])
def test_physical_publication_pin_mismatch_fails_before_claim(
    governance_pg,pin,changed_value,
):
    booking,preclaim,bound,template,evidence=context(governance_pg)
    altered=replace(bound,models=(
        replace(bound.models[0],**{pin:changed_value}),))
    with pytest.raises(ValueError,match="publication"):
        rehearse(governance_pg,booking,preclaim,altered,template,evidence)
    assert service(governance_pg).reservation(
        booking.reservation_id)["status"]=="ACTIVE"


def test_change_after_claim_requires_recovery_and_keeps_budget(governance_pg):
    booking,preclaim,bound,template,evidence=context(governance_pg)
    calls=[]
    def resolver():
        calls.append(True)
        if len(calls)==1:
            return bound,template,evidence
        changed=replace(bound,models=(replace(
            bound.models[0],serving_materialization_id=888),))
        return changed,template,evidence
    with pytest.raises(WarehouseClaimRecoveryRequired) as raised:
        rehearse(governance_pg,booking,preclaim,bound,template,evidence,
                 resolver=resolver)
    assert raised.value.reservation_id==booking.reservation_id
    assert len(calls)==2
    svc=service(governance_pg)
    assert svc.reservation(booking.reservation_id)["status"]=="EXECUTING"
    assert svc.usage().reserved_bytes_today==10*MIB


def test_second_worker_cannot_claim_same_reservation_twice(governance_pg):
    booking,preclaim,bound,template,evidence=context(governance_pg)
    rehearse(governance_pg,booking,preclaim,bound,template,evidence)
    with pytest.raises(ValueError,match="ACTIVE"):
        rehearse(governance_pg,booking,preclaim,bound,template,evidence)
    assert service(governance_pg).reservation(booking.reservation_id)["status"]=="EXECUTING"


def test_preclaim_failure_cannot_claim_or_create_provider_job(governance_pg):
    booking,preclaim,bound,template,evidence=context(governance_pg)
    invalid=replace(preclaim,state=PreclaimState.RELEASED_REVALIDATION_REQUIRED)
    with pytest.raises(ValueError,match="preclaim"):
        rehearse(governance_pg,booking,invalid,bound,template,evidence)
    assert service(governance_pg).reservation(booking.reservation_id)["status"]=="ACTIVE"


def test_cross_workspace_compilation_is_rejected_before_claim(governance_pg):
    booking,preclaim,bound,template,evidence=context(governance_pg)
    foreign=replace(evidence,workspace_id=governance_pg["foreign_workspace"])
    with pytest.raises(ValueError,match="publication"):
        rehearse(governance_pg,booking,preclaim,bound,template,foreign)
    assert service(governance_pg).reservation(booking.reservation_id)["status"]=="ACTIVE"


def test_no_provider_dispatch_release_completion_or_recovery_code():
    path=Path(__file__).parents[1]/"app/services/warehouse_sql_claim_rehearsal_service.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    names=[node.attr for node in ast.walk(tree) if isinstance(node,ast.Attribute)]
    assert "claim" in names
    assert not set(names)&{"execute","recover","release","complete","estimate"}
    imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(module and module.startswith(
        ("google","app.query_governance.providers.bigquery","psycopg"))
        for module in imports)
