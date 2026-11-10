"""Step 411C: connect the warehouse guard to real durable PostgreSQL claims.

All data lives in disposable PostgreSQL schemas. No BigQuery client, query
provider, SQL execution, or credential access is involved. The existing 0032
reservation repository remains the only owner of claim and recovery semantics.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from app.db import query_governance_repository as repository
from app.query_governance.providers.base import CompiledQuery
from app.query_governance.types import QueryEstimate
from app.warehouse.sql_claimed_reservation_guard import (
    inspect_claimed_warehouse_reservation,
)
from tests.test_warehouse_foundation import pg
from tests.test_query_governance_postgres import governance_pg
from tests.test_warehouse_sql_provider_metadata_verifier import binding


def compiled(workspace_id, bound, *, token="a", intent="b"):
    return CompiledQuery(
        workspace_id=workspace_id,
        request_token=token * 64,
        intent_sha256=intent * 64,
        sql="SELECT c0 FROM approved published model LIMIT 10",
        physical_tables=tuple(model.physical_table for model in bound.models),
        compiler_version=2,
    )


def owned_binding(workspace_id):
    prototype=binding()
    model=prototype.models[0]
    physical=model.physical_table.replace("dr_dev_w7.",f"dr_dev_w{workspace_id}.")
    return replace(prototype,models=(replace(model,physical_table=physical),))


def admitted(pg, *, token="a", intent="b"):
    with pg["connect"]() as conn:
        result=repository.reserve(
            conn,workspace_id=pg["workspace"],
            owner_key=pg["owner"]["owner_key"],
            consumer="SQL",provider="BIGQUERY",
            request_token=token * 64,intent_sha256=intent * 64,
            estimate=QueryEstimate(4096),requested_rows=10,
        )
    assert result["reservation"]["status"]=="ACTIVE"
    return result["reservation"]["reservation_id"]


def guarded(pg, claimed, *, owner=None, bound=None):
    owner=owner if owner is not None else pg["owner"]["owner_key"]
    bound=bound if bound is not None else owned_binding(pg["workspace"])
    return inspect_claimed_warehouse_reservation(
        workspace_id=pg["workspace"],owner_key=owner,consumer="SQL",
        reservation=claimed,binding=bound,query=compiled(pg["workspace"],bound),
    )


def test_real_claim_and_release_after_claim_is_forbidden(governance_pg):
    rid=admitted(governance_pg)
    with governance_pg["connect"]() as conn:
        claimed=repository.claim(conn,reservation_id=rid,
                                 owner_key=governance_pg["owner"]["owner_key"])
    checked=guarded(governance_pg,claimed)
    assert checked.reservation_id==rid
    assert checked.execution_authorized is False
    with governance_pg["connect"]() as conn:
        with pytest.raises(repository.ReservationConflict,match="RESERVATION_NOT_ACTIVE"):
            repository.release(conn,reservation_id=rid,
                               owner_key=governance_pg["owner"]["owner_key"])
    with governance_pg["connect"]() as conn:
        existing=repository.read_owned_reservation(
            conn,workspace_id=governance_pg["workspace"],
            owner_key=governance_pg["owner"]["owner_key"],reservation_id=rid)
        assert existing["status"]=="EXECUTING"
        assert existing["reserved_bytes"]==4096


def test_competing_workers_only_one_can_claim(governance_pg):
    rid=admitted(governance_pg)
    gate=Barrier(2)
    def worker():
        gate.wait()
        try:
            with governance_pg["connect"]() as conn:
                return ("claimed",repository.claim(
                    conn,reservation_id=rid,
                    owner_key=governance_pg["owner"]["owner_key"]))
        except repository.ReservationConflict as exc:
            return ("conflict",str(exc))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=[future.result() for future in [pool.submit(worker) for _ in range(2)]]
    assert sorted(kind for kind,_ in outcomes)==["claimed","conflict"]
    winner=next(detail for kind,detail in outcomes if kind=="claimed")
    assert guarded(governance_pg,winner).execution_authorized is False
    assert "RESERVATION_ALREADY_CLAIMED" in next(
        detail for kind,detail in outcomes if kind=="conflict")


def test_replay_after_completion_cannot_pass_claimed_guard(governance_pg):
    rid=admitted(governance_pg)
    with governance_pg["connect"]() as conn:
        claimed=repository.claim(conn,reservation_id=rid,
                                 owner_key=governance_pg["owner"]["owner_key"])
    assert guarded(governance_pg,claimed).claimed_reservation_consistent
    with governance_pg["connect"]() as conn:
        repository.complete(
            conn,reservation_id=rid,owner_key=governance_pg["owner"]["owner_key"],
            bytes_processed=100,bytes_billed=110,
            provider_job_id="synthetic-completed-warehouse",duration_ms=5)
    with governance_pg["connect"]() as conn:
        terminal=repository.read_owned_reservation(
            conn,workspace_id=governance_pg["workspace"],
            owner_key=governance_pg["owner"]["owner_key"],reservation_id=rid)
    with pytest.raises(ValueError,match="claimed warehouse reservation"):
        guarded(governance_pg,terminal)
    with governance_pg["connect"]() as conn:
        with pytest.raises(repository.ReservationConflict):
            repository.claim(conn,reservation_id=rid,
                             owner_key=governance_pg["owner"]["owner_key"])


def test_cross_workspace_owner_cannot_read_a_claim(governance_pg):
    rid=admitted(governance_pg)
    with governance_pg["connect"]() as conn:
        repository.claim(conn,reservation_id=rid,
                         owner_key=governance_pg["owner"]["owner_key"])
    with governance_pg["connect"]() as conn:
        with pytest.raises(repository.ReservationConflict):
            repository.read_owned_reservation(
                conn,workspace_id=governance_pg["workspace"],
                owner_key=governance_pg["other"]["owner_key"],
                reservation_id=rid)
        with pytest.raises(repository.ReservationConflict):
            repository.read_owned_reservation(
                conn,workspace_id=governance_pg["foreign_workspace"],
                owner_key=governance_pg["other"]["owner_key"],
                reservation_id=rid)


def test_claimed_unknown_provider_outcome_keeps_reservation_and_budget(governance_pg):
    rid=admitted(governance_pg)
    with governance_pg["connect"]() as conn:
        repository.claim(conn,reservation_id=rid,
                         owner_key=governance_pg["owner"]["owner_key"])
    # No external provider operation was made and no success/failure is
    # inferred. Simulate an abandoned worker simply by closing the connection.
    with governance_pg["connect"]() as conn:
        state=repository.read_owned_reservation(
            conn,workspace_id=governance_pg["workspace"],
            owner_key=governance_pg["owner"]["owner_key"],reservation_id=rid)
        usage=repository.usage_snapshot(
            conn,governance_pg["workspace"],
            governance_pg["owner"]["owner_key"])
    assert state["status"]=="EXECUTING"
    assert usage.reserved_bytes_today==4096
    assert usage.workspace_active_queries==1
    assert guarded(governance_pg,state).execution_authorized is False
