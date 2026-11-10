"""414D4a: authoritative owned logical-draft storage and re-resolution."""
from pathlib import Path

import psycopg
import pytest

from app.services import warehouse_model_service as models
from app.services.warehouse_query_attempt_service import (
    create_or_resume_warehouse_attempt,
)
from app.services.warehouse_sql_stored_draft_service import (
    logical_draft_digest,
    register_reviewed_logical_draft_without_dispatch,
    resolve_reviewed_logical_draft_without_dispatch,
)
from app.warehouse.sql_draft_planner import plan_draft
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg, configured, fact
from tests.test_warehouse_sql_admission_postgres import service

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def draft_registry_pg(modeling_pg):
    f=modeling_pg
    with f["connect"]() as conn:
        for name in (
            "0031_warehouse_reliability.sql",
            "0032_query_governance.sql",
            "0048_warehouse_query_attempt_registry.sql",
            "0049_warehouse_query_attempt_publication_pins.sql",
            "0050_warehouse_query_reservation_publication_links.sql",
            "0052_warehouse_query_attempt_logical_drafts.sql",
        ):
            conn.execute((ROOT/"migrations"/name).read_text(encoding="utf-8"))
    model,version,_,_=configured(f,fact())
    models.approve_version(f["workspace"],model,version,f["owner"])
    return f,model,version


def logical(f,model,version,sql=None):
    sql=sql or f"SELECT id FROM model_{model} LIMIT 5"
    catalog={
        "workspace_id":f["workspace"],
        "models":[{
            "model_id":model,"model_version_id":version,
            "model_type":"FACT","status":"APPROVED",
            "columns":[{"name":"id","role":"IDENTITY"}],
        }],
    }
    return plan_draft(sql,workspace_id=f["workspace"],catalog=catalog)


def attempt(f,model,version,sql=None):
    draft=logical(f,model,version,sql)
    handle=create_or_resume_warehouse_attempt(
        governance=service(f),
        logical_intent_sha256=logical_draft_digest(draft),
    )
    return handle,draft


def register(f,model,handle,sql=None):
    return register_reviewed_logical_draft_without_dispatch(
        governance=service(f),attempt_handle=handle.attempt_handle,
        model_id=model,
        logical_sql=sql or f"SELECT id FROM model_{model} LIMIT 5",
    )


def resolve(f,handle):
    return resolve_reviewed_logical_draft_without_dispatch(
        governance=service(f),attempt_handle=handle.attempt_handle,
    )


def test_persist_and_resolve_owned_reviewed_draft_from_handle_only(draft_registry_pg):
    f,model,version=draft_registry_pg
    handle,draft=attempt(f,model,version)
    registered=register(f,model,handle)
    retrieved=resolve(f,handle)
    assert retrieved.draft==draft
    assert registered.draft==draft
    assert retrieved.attempt_id==handle.attempt_id
    assert not retrieved.execution_authorized
    assert not retrieved.provider_dispatched
    with f["connect"]() as conn:
        row=conn.execute(
            "SELECT normalized_sql FROM warehouse_query_attempt_logical_drafts"
        ).fetchone()
        assert row==(draft.normalized_sql,)
        assert conn.execute(
            "SELECT count(*) FROM query_governance_reservations"
        ).fetchone()[0]==0


def test_same_draft_registration_is_idempotent(draft_registry_pg):
    f,model,version=draft_registry_pg
    handle,_=attempt(f,model,version)
    assert register(f,model,handle)==register(f,model,handle)
    with f["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_logical_drafts"
        ).fetchone()[0]==1


def test_changed_logical_query_cannot_rebind_same_attempt(draft_registry_pg):
    f,model,version=draft_registry_pg
    handle,_=attempt(f,model,version)
    register(f,model,handle)
    with pytest.raises(ValueError,match="DIGEST_MISMATCH"):
        register(f,model,handle,f"SELECT line FROM model_{model} LIMIT 5")
    with f["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_logical_drafts"
        ).fetchone()[0]==1


def test_arbitrary_physical_sql_is_rejected_at_planner(draft_registry_pg):
    f,model,version=draft_registry_pg
    handle,_=attempt(f,model,version)
    with pytest.raises(Exception):
        register(f,model,handle,
                 "SELECT id FROM other-project.somewhere.secret LIMIT 5")
    with f["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_logical_drafts"
        ).fetchone()[0]==0


def test_wrong_attempt_digest_cannot_register(draft_registry_pg):
    f,model,version=draft_registry_pg
    handle=create_or_resume_warehouse_attempt(
        governance=service(f),logical_intent_sha256="a"*64,
    )
    with pytest.raises(ValueError,match="DIGEST_MISMATCH"):
        register(f,model,handle)


def test_foreign_owner_cannot_resolve_stored_draft(draft_registry_pg):
    f,model,version=draft_registry_pg
    handle,_=attempt(f,model,version)
    register(f,model,handle)
    from app.db.query_governance_repository import ReservationConflict
    with pytest.raises(ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        resolve_reviewed_logical_draft_without_dispatch(
            governance=service(f,owner=f["other"]),
            attempt_handle=handle.attempt_handle,
        )


def test_logical_draft_rows_immutable(draft_registry_pg):
    f,model,version=draft_registry_pg
    handle,_=attempt(f,model,version)
    register(f,model,handle)
    with pytest.raises(psycopg.Error,match="immutable"):
        with f["connect"]() as conn:
            conn.execute(
                """UPDATE warehouse_query_attempt_logical_drafts
                SET row_limit=row_limit WHERE attempt_id=%s""",
                (handle.attempt_id,),
            )
    with pytest.raises(psycopg.Error,match="immutable"):
        with f["connect"]() as conn:
            conn.execute(
                "DELETE FROM warehouse_query_attempt_logical_drafts WHERE attempt_id=%s",
                (handle.attempt_id,),
            )


def test_resolve_missing_draft_fails_closed(draft_registry_pg):
    f,model,version=draft_registry_pg
    handle,_=attempt(f,model,version)
    with pytest.raises(ValueError,match="NOT_REGISTERED"):
        resolve(f,handle)


def test_no_execution_or_claim_calls():
    text=(ROOT/"app/services/warehouse_sql_stored_draft_service.py").read_text(
        encoding="utf-8",
    )
    assert "execution_authorized: bool = False" in text
    assert "provider_dispatched: bool = False" in text
    assert "provider.execute(" not in text
    assert "governance.claim(" not in text
