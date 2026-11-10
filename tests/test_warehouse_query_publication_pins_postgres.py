"""414C1: reject unowned, stale and unproven publication pin inserts.

This suite covers the additive PostgreSQL pin schema's fail-closed cases.
A positive publication/retention race suite is required in 414C2 before
dispatch can be contemplated.
"""
from pathlib import Path
import ast

import psycopg
import pytest

from app.services.warehouse_query_attempt_service import create_or_resume_warehouse_attempt
from tests.test_query_governance_postgres import governance_pg
from tests.test_warehouse_foundation import pg, draft
from app.db import warehouse_repository
from app.schemas.warehouse import ResourceRegistration
from tests.test_warehouse_sql_admission_postgres import service

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def pin_pg(governance_pg):
    with governance_pg["connect"]() as conn:
        conn.execute((ROOT/"migrations/0048_warehouse_query_attempt_registry.sql").read_text(
            encoding="utf-8"))
        conn.execute((ROOT/"migrations/0049_warehouse_query_attempt_publication_pins.sql").read_text(
            encoding="utf-8"))
    return governance_pg


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
    # This is a *database trigger* negative test, not an approval test.
    # Insert a structurally valid synthetic REGISTERED resource directly in
    # the disposable schema; production service approval remains mandatory.
    mid,version=draft(pin_pg)
    registration=ResourceRegistration(
        environment="development",location="asia-south1",generation=1,
        physical_reference=dict(
            project="synthetic-project",dataset="synthetic_dataset",
            table="synthetic_generation_1",
        ),
    )
    with pin_pg["connect"]() as conn:
        res=warehouse_repository.register_resource(
            conn,pin_pg["workspace"],mid,version,
            pin_pg["owner"]["user_id"],registration,
        )
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
