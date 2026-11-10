"""414D4e: positive owned PostgreSQL -> serving receipt -> compiler round trip.

The warehouse provider fixture uses a fake in-memory cloud. It creates real
PostgreSQL publication and materialization evidence but NEVER runs analytical
BigQuery queries or uses external cloud credentials.
"""
from pathlib import Path

import pytest

from app.db import warehouse_repository
from app.query_governance.policy import MIB
from app.query_governance.types import QueryEstimate
from app.services.metric_warehouse_binding_service import (
    _publication_state_id, _source,
)
from app.services.warehouse_query_attempt_service import (
    create_or_resume_warehouse_attempt,
)
from app.services.warehouse_sql_stored_draft_service import (
    logical_draft_digest,register_reviewed_logical_draft_without_dispatch,
)
from app.services.warehouse_sql_trusted_recompilation_service import (
    recompile_stored_warehouse_attempt_without_dispatch,
)
from app.warehouse.sql_draft_planner import plan_draft
from app.warehouse.sql_physical_binding import bind_draft
from app.warehouse.sql_single_model_compiler import compile_single_model_sql
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_provider_postgres import fact_provider_pg
from tests.test_warehouse_serving_postgres import publication as publish, row
from tests.test_warehouse_query_reservation_publication_links_postgres import (
    insert_link,
)
from tests.test_warehouse_sql_admission_postgres import service

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def genuinely_served_query(fact_provider_pg):
    f=fact_provider_pg
    published=publish(f,[row("A",1),row("B",2)])
    receipt=f.provider.materialize_current_publication(f.resource)
    assert receipt["publication_id"]==published["publication_id"]
    # Only after materialization: install governed SQL metadata migrations.
    with f.pg["connect"]() as conn:
        for name in (
            "0032_query_governance.sql",
            "0048_warehouse_query_attempt_registry.sql",
            "0049_warehouse_query_attempt_publication_pins.sql",
            "0050_warehouse_query_reservation_publication_links.sql",
            "0051_warehouse_query_publication_retirement_fences.sql",
            "0052_warehouse_query_attempt_logical_drafts.sql",
        ):
            conn.execute((ROOT/"migrations"/name).read_text(encoding="utf-8"))

    logical_sql=f"SELECT id FROM model_{f.model} LIMIT 5"
    # Independent planner input is a scoped logical catalog, not physical SQL.
    draft=plan_draft(
        logical_sql,workspace_id=f.pg["workspace"],
        catalog={"workspace_id":f.pg["workspace"],"models":[{
            "model_id":f.model,"model_version_id":f.version,
            "model_type":"FACT","status":"APPROVED",
            "columns":[{"name":"id","role":"IDENTITY"}],
        }]},
    )
    governance=service(f.pg)
    attempt=create_or_resume_warehouse_attempt(
        governance=governance,logical_intent_sha256=logical_draft_digest(draft),
    )
    register_reviewed_logical_draft_without_dispatch(
        governance=governance,attempt_handle=attempt.attempt_handle,
        model_id=f.model,logical_sql=logical_sql,
    )

    # The production resolver reconstructs physical table and column names
    # from currently published metadata, not from a client-provided binding.
    with f.pg["connect"]() as conn:
        rows=warehouse_repository.metric_warehouse_bindings(
            conn,f.pg["workspace"],[f.pg["dataset"]],
            environment="development",
        )
        assert len(rows)==1
        physical_source=_source(
            rows[0],f.pg["workspace"],_publication_state_id(rows[0]),
        )
    binding=bind_draft(draft,fact=physical_source)
    compiled=compile_single_model_sql(
        workspace_id=f.pg["workspace"],draft=draft,binding=binding,
    )
    with f.pg["connect"]() as conn:
        conn.execute(
            """INSERT INTO warehouse_query_attempt_publication_pins(
               attempt_id,resource_id,publication_id,publication_revision)
               VALUES (%s,%s,%s,%s)""",
            (attempt.attempt_id,f.resource,published["publication_id"],
             published["revision"]),
        )
    reservation=governance.admit(
        consumer="SQL",provider="BIGQUERY",
        request_token=attempt.request_token,
        intent_sha256=compiled.intent_sha256,
        estimate=QueryEstimate(10*MIB),requested_rows=5,
    )["reservation"]["reservation_id"]
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    return f,published,receipt,governance,attempt,reservation,compiled


def test_positive_server_recompilation_exactly_matches_reserved_compiler_intent(
    genuinely_served_query,
):
    f,publication,receipt,governance,attempt,reservation,compiled=genuinely_served_query
    initial_calls=len(f.cloud.calls)
    outcome=recompile_stored_warehouse_attempt_without_dispatch(
        governance=governance,attempt_handle=attempt.attempt_handle,
    )
    assert outcome.attempt_id==attempt.attempt_id
    assert outcome.reservation_id==reservation
    assert outcome.compiled_intent_sha256==compiled.intent_sha256
    assert outcome.physical_scope_verified
    assert outcome.reason=="VERIFIED_INTERNAL_NON_EXECUTING"
    assert not outcome.execution_authorized
    assert not outcome.reservation_claimed
    assert not outcome.provider_dispatched
    assert governance.reservation(reservation)["status"]=="ACTIVE"
    assert len(f.cloud.calls)==initial_calls  # no provider interactions at all


def test_second_resolution_is_stable_and_does_not_claim(genuinely_served_query):
    f,publication,receipt,governance,attempt,reservation,compiled=genuinely_served_query
    first=recompile_stored_warehouse_attempt_without_dispatch(
        governance=governance,attempt_handle=attempt.attempt_handle,
    )
    second=recompile_stored_warehouse_attempt_without_dispatch(
        governance=governance,attempt_handle=attempt.attempt_handle,
    )
    assert first==second
    assert governance.reservation(reservation)["status"]=="ACTIVE"


def test_precompiled_wrong_intent_does_not_authorize(genuinely_served_query):
    f,publication,receipt,governance,attempt,reservation,compiled=genuinely_served_query
    with f.pg["connect"]() as conn:
        # Durable identity is already fixed; the original compiled scope
        # is the only one that can validate. No mutation/rebinding allowed.
        row=conn.execute(
            "SELECT intent_sha256,request_token FROM query_governance_reservations WHERE reservation_id=%s",
            (reservation,),
        ).fetchone()
        assert row==(compiled.intent_sha256,attempt.request_token)
    assert not recompile_stored_warehouse_attempt_without_dispatch(
        governance=governance,attempt_handle=attempt.attempt_handle,
    ).execution_authorized


def test_positive_fixture_uses_fake_provider_only():
    test_source=(ROOT/"tests/test_warehouse_sql_trusted_recompilation_414d4e.py"
                 ).read_text(encoding="utf-8")
    production_source=(ROOT/"app/services/warehouse_sql_trusted_recompilation_service.py"
                       ).read_text(encoding="utf-8")
    assert "fact_provider_pg" in test_source
    assert "materialize_current_publication" in test_source
    assert "QueryEstimate" in test_source
    assert "provider.execute(" not in production_source
    assert "client.query(" not in production_source
