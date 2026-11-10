"""Step 413B: real PostgreSQL ACTIVE admission and no query dispatch."""
from dataclasses import replace
import ast
from pathlib import Path

import pytest

from app.db import query_governance_repository as repository
from app.query_governance.policy import MIB, QueryPolicy
from app.query_governance.types import Decision, QueryEstimate
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_admission_service import admit_warehouse_query_attempt
from app.services.warehouse_sql_compiled_evidence_service import WarehouseCompiledEvidence
from app.services.warehouse_sql_dry_run_service import (
    WarehouseDryRunEstimate, prepare_warehouse_query_attempt,
)
from tests.test_query_governance_postgres import governance_pg
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_sql_single_model_compiler import compile_with


def dry(*, token="a", estimate=10*MIB):
    evidence=WarehouseCompiledEvidence(workspace_id=1,model_count=1)
    # The published compile example belongs to workspace 7. Change the
    # physical table only for these disposable workspace-1 governance tests.
    base=compile_with()
    from app.query_governance.providers.base import CompiledQuery
    name=base.physical_tables[0].replace("dr_dev_w7.","dr_dev_w1.")
    template=CompiledQuery(
        workspace_id=1,request_token=base.request_token,
        intent_sha256=base.intent_sha256,sql=base.sql.replace(
            base.physical_tables[0],name),
        physical_tables=(name,),compiler_version=2,
    )
    attempt=prepare_warehouse_query_attempt(
        template=template,evidence=evidence,random_token=lambda:token*64,
    )
    return WarehouseDryRunEstimate(attempt,QueryEstimate(estimate))


def service(pg, *, policy=None, owner=None):
    return QueryGovernanceService(
        pg["workspace"],owner if owner is not None else pg["owner"],
        policy=policy or QueryPolicy(),
    )


def admit(pg, *, run=None, policy=None, svc=None, rows=5):
    return admit_warehouse_query_attempt(
        dry_run=dry() if run is None else run,
        governance=service(pg,policy=policy) if svc is None else svc,
        requested_rows=rows,
    )


def test_durable_active_reservation_uses_existing_accounting(governance_pg):
    result=admit(governance_pg)
    assert result.decision==Decision.ALLOWED
    assert result.reservation_id>0
    assert result.reserved_bytes==10*MIB
    assert not result.execution_authorized
    with governance_pg["connect"]() as conn:
        persisted=repository.read_owned_reservation(
            conn,workspace_id=governance_pg["workspace"],
            owner_key=governance_pg["owner"]["owner_key"],
            reservation_id=result.reservation_id,
        )
        assert persisted["status"]=="ACTIVE"
        assert persisted["provider"]=="BIGQUERY"
        assert persisted["request_token"]==result.query.request_token
        assert persisted["intent_sha256"]==result.query.intent_sha256
        usage=repository.usage_snapshot(
            conn,governance_pg["workspace"],governance_pg["owner"]["owner_key"])
        assert usage.reserved_bytes_today==10*MIB
        assert usage.workspace_active_queries==1
    assert service(governance_pg).release(result.reservation_id)["status"]=="RELEASED"


def test_identical_attempt_reuses_only_active_reservation(governance_pg):
    attempt=dry()
    first=admit(governance_pg,run=attempt)
    second=admit(governance_pg,run=attempt)
    assert second.reused
    assert second.reservation_id==first.reservation_id
    assert second.execution_authorized is False


def test_distinct_query_attempts_create_distinct_durable_ids(governance_pg):
    first=admit(governance_pg,run=dry(token="a"))
    second=admit(governance_pg,run=dry(token="b"))
    assert first.reservation_id!=second.reservation_id
    assert first.query.intent_sha256==second.query.intent_sha256
    assert first.query.request_token!=second.query.request_token
    assert service(governance_pg).usage().reserved_bytes_today==20*MIB


def test_token_reuse_with_changed_estimate_fails_closed(governance_pg):
    admit(governance_pg,run=dry(estimate=10*MIB))
    with pytest.raises(repository.ReservationConflict,match="REQUEST_TOKEN_CONFLICT"):
        admit(governance_pg,run=dry(estimate=11*MIB))


def test_completed_attempt_is_not_reattached_as_active(governance_pg):
    attempt=dry()
    first=admit(governance_pg,run=attempt)
    svc=service(governance_pg)
    svc.release(first.reservation_id)
    with pytest.raises(ValueError,match="ACTIVE"):
        admit(governance_pg,run=attempt)


def test_workspace_owner_is_checked_by_existing_repository(governance_pg):
    forged=service(governance_pg,owner=governance_pg["other"])
    with pytest.raises(repository.ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        admit(governance_pg,svc=forged)


def test_workspace_concurrency_blocks_second_attempt(governance_pg):
    policy=QueryPolicy(workspace_concurrency=1,global_concurrency=1)
    first=admit(governance_pg,run=dry(token="a"),policy=policy)
    second=admit(governance_pg,run=dry(token="b"),policy=policy)
    assert first.decision==Decision.ALLOWED
    assert second.decision==Decision.BLOCKED
    assert second.reservation_id is None
    assert second.reserved_bytes==0
    assert not second.execution_authorized


def test_review_required_is_not_reserved(governance_pg):
    result=admit(governance_pg,run=dry(estimate=300*MIB))
    assert result.decision==Decision.REVIEW_REQUIRED
    assert result.reservation_id is None
    assert result.execution_authorized is False
    assert service(governance_pg).usage().reserved_bytes_today==0


def test_cross_workspace_dry_run_is_rejected_before_db(governance_pg):
    original=dry()
    other_table=original.query.physical_tables[0].replace(
        "dr_dev_w1.","dr_dev_w8.")
    foreign_query=replace(
        original.query,
        workspace_id=8,
        physical_tables=(other_table,),
        sql=original.query.sql.replace(
            original.query.physical_tables[0],other_table),
    )
    changed=replace(original,query=foreign_query)
    with pytest.raises(ValueError,match="workspace"):
        admit(governance_pg,run=changed)


@pytest.mark.parametrize("change",[
    {"admission_created":True},
    {"execution_authorized":True},
    {"metadata_and_publication_checked":False},
])
def test_unsafe_dry_run_evidence_cannot_admit(governance_pg,change):
    changed=replace(dry(),**change)
    with pytest.raises(ValueError,match="non-executing"):
        admit(governance_pg,run=changed)


def test_module_never_claims_or_dispatches_provider_sql():
    source=(Path(__file__).parents[1]/
            "app/services/warehouse_sql_admission_service.py")
    tree=ast.parse(source.read_text(encoding="utf-8"))
    methods=[n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)]
    assert not set(methods)&{"claim","execute","recover","complete"}
    imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(n and n.startswith(("google","app.query_governance.providers.bigquery"))
                   for n in imports)
