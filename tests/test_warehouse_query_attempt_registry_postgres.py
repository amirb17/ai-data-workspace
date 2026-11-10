"""414B: real PostgreSQL attempt identity and concurrency, zero provider IO."""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import psycopg
import pytest

from app.db import warehouse_query_attempt_repository as registry
from app.services.warehouse_query_attempt_service import (
    create_or_resume_warehouse_attempt,
    inspect_owned_warehouse_attempt,
)
from tests.test_query_governance_postgres import governance_pg
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_sql_admission_postgres import service

ROOT=Path(__file__).resolve().parents[1]
MIGRATION=ROOT/"migrations/0048_warehouse_query_attempt_registry.sql"


@pytest.fixture
def attempts_pg(governance_pg):
    with governance_pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
    return governance_pg


def test_create_and_repeat_handle_reuses_server_token(attempts_pg):
    svc=service(attempts_pg)
    first=create_or_resume_warehouse_attempt(
        governance=svc,logical_intent_sha256="a"*64)
    again=create_or_resume_warehouse_attempt(
        governance=svc,logical_intent_sha256="a"*64,
        attempt_handle=first.attempt_handle)
    assert len(first.attempt_handle)==32
    assert len(first.request_token)==64
    assert not first.reused and again.reused
    assert first.attempt_id==again.attempt_id
    assert first.request_token==again.request_token
    assert first.logical_intent_sha256==again.logical_intent_sha256
    assert not first.execution_authorized
    assert not first.reservation_created
    assert inspect_owned_warehouse_attempt(
        governance=svc,attempt_handle=first.attempt_handle
    ).request_token==first.request_token
    with attempts_pg["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_registry"
        ).fetchone()[0]==1
        assert conn.execute(
            "SELECT count(*) FROM query_governance_reservations"
        ).fetchone()[0]==0


def test_conflicting_digest_does_not_replace_token(attempts_pg):
    svc=service(attempts_pg)
    initial=create_or_resume_warehouse_attempt(
        governance=svc,logical_intent_sha256="a"*64)
    with pytest.raises(registry.WarehouseAttemptConflict,
                       match="WAREHOUSE_ATTEMPT_INTENT_CONFLICT"):
        create_or_resume_warehouse_attempt(
            governance=svc,logical_intent_sha256="b"*64,
            attempt_handle=initial.attempt_handle)
    assert inspect_owned_warehouse_attempt(
        governance=svc,attempt_handle=initial.attempt_handle
    ).request_token==initial.request_token


def test_foreign_owner_cannot_issue_or_read_handle(attempts_pg):
    original=service(attempts_pg)
    first=create_or_resume_warehouse_attempt(
        governance=original,logical_intent_sha256="a"*64)
    foreign=service(attempts_pg,owner=attempts_pg["other"])
    from app.db.query_governance_repository import ReservationConflict
    with pytest.raises(ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        create_or_resume_warehouse_attempt(
            governance=foreign,logical_intent_sha256="a"*64,
            attempt_handle=first.attempt_handle)
    with pytest.raises(ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        inspect_owned_warehouse_attempt(
            governance=foreign,attempt_handle=first.attempt_handle)


def test_other_owned_workspace_cannot_reuse_foreign_handle(attempts_pg):
    first=create_or_resume_warehouse_attempt(
        governance=service(attempts_pg),logical_intent_sha256="a"*64)
    from app.services.query_governance_service import QueryGovernanceService
    other=QueryGovernanceService(
        attempts_pg["foreign_workspace"],attempts_pg["other"])
    with pytest.raises(registry.WarehouseAttemptConflict,
                       match="WAREHOUSE_ATTEMPT_NOT_OWNED"):
        create_or_resume_warehouse_attempt(
            governance=other,logical_intent_sha256="a"*64,
            attempt_handle=first.attempt_handle)
    with pytest.raises(registry.WarehouseAttemptConflict,
                       match="WAREHOUSE_ATTEMPT_NOT_FOUND"):
        inspect_owned_warehouse_attempt(
            governance=other,attempt_handle=first.attempt_handle)


def test_two_distinct_handles_do_not_share_token(attempts_pg):
    svc=service(attempts_pg)
    a=create_or_resume_warehouse_attempt(
        governance=svc,logical_intent_sha256="a"*64)
    b=create_or_resume_warehouse_attempt(
        governance=svc,logical_intent_sha256="a"*64)
    assert a.attempt_handle!=b.attempt_handle
    assert a.request_token!=b.request_token
    assert a.attempt_id!=b.attempt_id


def test_two_workers_same_handle_converge_on_one_row(attempts_pg):
    svc=service(attempts_pg)
    handle="e"*32
    gate=Barrier(2)
    def worker():
        gate.wait()
        return create_or_resume_warehouse_attempt(
            governance=svc,logical_intent_sha256="a"*64,
            attempt_handle=handle)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(worker) for _ in range(2)]
        results=[f.result() for f in futures]
    assert results[0].request_token==results[1].request_token
    assert results[0].attempt_id==results[1].attempt_id
    assert sorted(x.reused for x in results)==[False,True]
    with attempts_pg["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_registry"
        ).fetchone()[0]==1


@pytest.mark.parametrize("bad",["nothex","g"*32,"a"*31,"A"*32])
def test_invalid_handles_rejected_without_insert(attempts_pg,bad):
    with pytest.raises(ValueError,match="identity"):
        create_or_resume_warehouse_attempt(
            governance=service(attempts_pg),
            logical_intent_sha256="a"*64,attempt_handle=bad)
    with attempts_pg["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_registry"
        ).fetchone()[0]==0


def test_attempt_table_rejects_mutation(attempts_pg):
    first=create_or_resume_warehouse_attempt(
        governance=service(attempts_pg),logical_intent_sha256="a"*64)
    with pytest.raises(psycopg.Error,match="immutable"):
        with attempts_pg["connect"]() as conn:
            conn.execute(
                "UPDATE warehouse_query_attempt_registry SET "
                "logical_intent_sha256=%s WHERE attempt_id=%s",
                ("b"*64,first.attempt_id))
    with pytest.raises(psycopg.Error,match="immutable"):
        with attempts_pg["connect"]() as conn:
            conn.execute(
                "DELETE FROM warehouse_query_attempt_registry WHERE attempt_id=%s",
                (first.attempt_id,))


def test_service_never_calls_query_admission_or_provider():
    path=ROOT/"app/services/warehouse_query_attempt_service.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    calls=[n.func.attr for n in ast.walk(tree)
           if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
    assert not set(calls)&{"admit","claim","execute","estimate","recover",
                            "release","complete","fail"}
    assert "register_attempt" in calls
    imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(n and n.startswith(("google","psycopg"))
                   for n in imports)
