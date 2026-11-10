"""414D4c: real owned publication and serving-receipt resolution."""
from pathlib import Path

import pytest

from app.services.warehouse_query_attempt_service import create_or_resume_warehouse_attempt
from app.services.warehouse_sql_stored_draft_service import (
    logical_draft_digest,register_reviewed_logical_draft_without_dispatch,
)
from app.services.warehouse_sql_stored_publication_evidence_service import (
    resolve_stored_publication_evidence_without_dispatch as resolve,
)
from app.warehouse.sql_draft_planner import plan_draft
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_reliability import reliability_pg
from tests.test_warehouse_query_reservation_publication_links_postgres import (
    linked_pg,insert_link,
)
from tests.test_warehouse_sql_admission_postgres import service
from app.query_governance.policy import MIB
from app.query_governance.types import QueryEstimate

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def evidence_pg(linked_pg):
    f,publication=linked_pg
    with f.pg["connect"]() as conn:
        conn.execute(
            (ROOT/"migrations/0052_warehouse_query_attempt_logical_drafts.sql")
            .read_text(encoding="utf-8")
        )
    sql=f"SELECT id FROM model_{f.model} LIMIT 5"
    reviewed=plan_draft(
        sql,workspace_id=f.pg["workspace"],
        catalog={"workspace_id":f.pg["workspace"],"models":[{
            "model_id":f.model,"model_version_id":f.version,
            "model_type":"FACT","status":"APPROVED",
            "columns":[{"name":"id","role":"IDENTITY"}],
        }]},
    )
    governance=service(f.pg)
    attempt=create_or_resume_warehouse_attempt(
        governance=governance,logical_intent_sha256=logical_draft_digest(reviewed),
    )
    register_reviewed_logical_draft_without_dispatch(
        governance=governance,attempt_handle=attempt.attempt_handle,
        model_id=f.model,logical_sql=sql,
    )
    with f.pg["connect"]() as conn:
        conn.execute(
            """INSERT INTO warehouse_query_attempt_publication_pins
            (attempt_id,resource_id,publication_id,publication_revision)
            VALUES (%s,%s,%s,%s)""",
            (attempt.attempt_id,f.resource,publication["publication_id"],
             publication["revision"]),
        )
    reservation=governance.admit(
        consumer="SQL",provider="BIGQUERY",request_token=attempt.request_token,
        intent_sha256="b"*64,estimate=QueryEstimate(10*MIB),requested_rows=5,
    )["reservation"]["reservation_id"]
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    return f,publication,governance,attempt,reservation


def insert_receipt(f,publication):
    with f.pg["connect"]() as conn:
        return conn.execute(
            """INSERT INTO warehouse_serving_materializations(
               workspace_id,warehouse_model_id,warehouse_model_version_id,
               resource_id,publication_id,publication_revision,generation,
               materialization_token,intent_sha256,configuration_sha256,
               schema_sha256,source_vector_sha256,provider_job_id,
               serving_sha256,row_count,bytes_processed,bytes_billed
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING serving_materialization_id""",
            (f.pg["workspace"],f.model,f.version,f.resource,
             publication["publication_id"],publication["revision"],1,
             "a"*64,"b"*64,"c"*64,"d"*64,"e"*64,
             "test-serving-receipt","f"*64,1,0,0),
        ).fetchone()[0]


def test_missing_serving_receipt_denied(evidence_pg):
    f,pub,gov,attempt,reservation=evidence_pg
    with pytest.raises(ValueError,match="WAREHOUSE_SERVING_RECEIPT_NOT_READY"):
        resolve(governance=gov,attempt_handle=attempt.attempt_handle)
    assert gov.reservation(reservation)["status"]=="ACTIVE"


def test_existing_immutable_receipt_resolves_metadata_without_dispatch(evidence_pg):
    f,pub,gov,attempt,reservation=evidence_pg
    receipt_id=insert_receipt(f,pub)
    evidence=resolve(governance=gov,attempt_handle=attempt.attempt_handle)
    assert evidence.attempt_id==attempt.attempt_id
    assert evidence.reservation_id==reservation
    assert evidence.resource_id==f.resource
    assert evidence.publication_id==pub["publication_id"]
    assert evidence.serving_materialization_id==receipt_id
    assert evidence.schema_sha256=="d"*64
    assert evidence.normalized_logical_sql.startswith("SELECT")
    assert not evidence.execution_authorized
    assert not evidence.provider_dispatched
    assert gov.reservation(reservation)["status"]=="ACTIVE"


def test_foreign_owner_cannot_resolve(evidence_pg):
    f,pub,gov,attempt,reservation=evidence_pg
    insert_receipt(f,pub)
    from app.db.query_governance_repository import ReservationConflict
    with pytest.raises(ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        resolve(governance=service(f.pg,owner=f.pg["other"]),
                attempt_handle=attempt.attempt_handle)


def test_released_reservation_not_eligible(evidence_pg):
    f,pub,gov,attempt,reservation=evidence_pg
    insert_receipt(f,pub)
    gov.release(reservation)
    with pytest.raises(ValueError,match="DEPENDENCY_NOT_READY"):
        resolve(governance=gov,attempt_handle=attempt.attempt_handle)


def test_no_provider_or_claim_execution_surface():
    src=(ROOT/"app/services/warehouse_sql_stored_publication_evidence_service.py"
         ).read_text(encoding="utf-8")
    assert "execution_authorized: bool = False" in src
    assert "provider_dispatched: bool = False" in src
    assert "provider.execute(" not in src
    assert "governance.claim(" not in src
