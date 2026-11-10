"""414C1: reject unowned, stale and unproven publication pin inserts.

This suite covers the additive PostgreSQL pin schema's fail-closed cases.
A positive publication/retention race suite is required in 414C2 before
dispatch can be contemplated.
"""
from pathlib import Path

import psycopg
import pytest

from app.services.warehouse_query_attempt_service import create_or_resume_warehouse_attempt
from tests.test_warehouse_modeling import modeling_pg, configured
from tests.test_warehouse_foundation import pg, resource
from app.services import warehouse_model_service as models
from tests.test_warehouse_sql_admission_postgres import service

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def pin_pg(modeling_pg):
    # Reuse the real, approved FACT/DIMENSION modeling fixture instead of
    # fabricating a resource that the database rightly refuses to register.
    with modeling_pg["connect"]() as conn:
        for name in (
            "0031_warehouse_reliability.sql",
            "0032_query_governance.sql",
            "0048_warehouse_query_attempt_registry.sql",
            "0049_warehouse_query_attempt_publication_pins.sql",
        ):
            conn.execute((ROOT/"migrations"/name).read_text(encoding="utf-8"))
    return modeling_pg


def test_pin_table_created_empty_and_does_not_admit_queries(pin_pg):
    with pin_pg["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_publication_pins"
        ).fetchone()[0]==0
        assert conn.execute(
            "SELECT count(*) FROM query_governance_reservations"
        ).fetchone()[0]==0


def test_unregistered_resource_is_rejected_without_persisting(pin_pg):
    attempt=create_or_resume_warehouse_attempt(
        governance=service(pin_pg),logical_intent_sha256="a"*64)
    with pytest.raises(psycopg.Error):
        with pin_pg["connect"]() as conn:
            conn.execute(
                """INSERT INTO warehouse_query_attempt_publication_pins
                (attempt_id,resource_id,publication_id,publication_revision)
                VALUES (%s,%s,%s,%s)""",
                (attempt.attempt_id,999999,999999,1),
            )
    with pin_pg["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_publication_pins"
        ).fetchone()[0]==0


def test_registered_resource_without_publication_head_fails_closed(pin_pg):
    # Create a genuinely configured and approved model; use the ordinary
    # registration service so all PostgreSQL approval guards stay enforced.
    mid,version,_,_=configured(pin_pg)
    models.approve_version(pin_pg["workspace"],mid,version,pin_pg["owner"])
    res=resource(pin_pg,mid,version)
    attempt=create_or_resume_warehouse_attempt(
        governance=service(pin_pg),logical_intent_sha256="a"*64)
    with pytest.raises(psycopg.Error,match="Current warehouse publication head required"):
        with pin_pg["connect"]() as conn:
            conn.execute(
                """INSERT INTO warehouse_query_attempt_publication_pins
                   (attempt_id,resource_id,publication_id,publication_revision)
                   VALUES (%s,%s,%s,%s)""",
                (attempt.attempt_id,res["resource_id"],999999,1),
            )
    with pin_pg["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_publication_pins"
        ).fetchone()[0]==0


def test_pin_migration_forbids_mutation_and_truncate():
    sql=(ROOT/"migrations/0049_warehouse_query_attempt_publication_pins.sql").read_text(
        encoding="utf-8")
    assert "TG_OP <> 'INSERT'" in sql
    assert "BEFORE INSERT OR UPDATE OR DELETE" in sql
    assert "BEFORE TRUNCATE" in sql
    assert "FOR SHARE" in sql
    assert "warehouse_publication_heads" in sql
    assert "warehouse_query_attempt_registry" in sql
    assert "owner=attempt_row.owner_key" in sql
    assert "environment<>'development'" in sql


def test_no_provider_execution_was_added_for_publication_pin_work():
    source=(ROOT/"migrations/0049_warehouse_query_attempt_publication_pins.sql").read_text(
        encoding="utf-8").lower()
    assert "bigquery" not in source
    assert "execute provider" not in source
