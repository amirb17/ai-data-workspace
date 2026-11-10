"""414C3a: durable query reservation/publication dependency boundary.

Only isolated PostgreSQL metadata; no external cloud query, cleanup or
execution permission is created by these tests.
"""
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest

from app.query_governance.policy import MIB
from app.query_governance.types import QueryEstimate
from app.services.warehouse_query_attempt_service import create_or_resume_warehouse_attempt
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_reliability import reliability_pg, registered, intent, execute
from tests.test_warehouse_sql_admission_postgres import service

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def linked_pg(reliability_pg):
    f=reliability_pg
    with f.pg["connect"]() as conn:
        for name in (
            "0048_warehouse_query_attempt_registry.sql",
            "0049_warehouse_query_attempt_publication_pins.sql",
            "0050_warehouse_query_reservation_publication_links.sql",
        ):
            conn.execute((ROOT/"migrations"/name).read_text(encoding="utf-8"))
    manifests=registered(f)
    publication=execute(f,intent(f,manifests),manifests)
    return f,publication


def setup_attempt(f,publication,*,token=None,pin=True):
    svc=service(f.pg)
    attempt=create_or_resume_warehouse_attempt(
        governance=svc,logical_intent_sha256="a"*64,
    )
    if pin:
        with f.pg["connect"]() as conn:
            conn.execute(
                """INSERT INTO warehouse_query_attempt_publication_pins
                (attempt_id,resource_id,publication_id,publication_revision)
                VALUES (%s,%s,%s,%s)""",
                (attempt.attempt_id,f.resource,publication["publication_id"],
                 publication["revision"]),
            )
    admitted=svc.admit(
        consumer="SQL",provider="BIGQUERY",
        request_token=attempt.request_token if token is None else token,
        intent_sha256="b"*64,
        estimate=QueryEstimate(10*MIB),requested_rows=5,
    )
    return svc,attempt,admitted["reservation"]["reservation_id"]


def insert_link(conn,attempt,reservation):
    conn.execute(
        """INSERT INTO warehouse_query_attempt_reservation_links
        (attempt_id,reservation_id) VALUES (%s,%s)""",
        (attempt.attempt_id,reservation),
    )


def dependency(conn,resource,publication):
    return conn.execute(
        "SELECT warehouse_query_publication_dependency_present(%s,%s)",
        (resource,publication),
    ).fetchone()[0]


def test_active_and_executing_reservation_hold_snapshot_dependency(linked_pg):
    f,publication=linked_pg
    svc,attempt,reservation=setup_attempt(f,publication)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
        assert dependency(conn,f.resource,publication["publication_id"]) is True
    assert svc.claim(reservation)["status"]=="EXECUTING"
    with f.pg["connect"]() as conn:
        assert dependency(conn,f.resource,publication["publication_id"]) is True
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_reservation_links"
        ).fetchone()[0]==1
    # No provider lookup/execution or terminal accounting in this test.


def test_failed_and_released_reservations_do_not_hold_dependency(linked_pg):
    f,publication=linked_pg
    svc,attempt,reservation=setup_attempt(f,publication)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    assert svc.release(reservation)["status"]=="RELEASED"
    with f.pg["connect"]() as conn:
        assert dependency(conn,f.resource,publication["publication_id"]) is False

    svc2,attempt2,reservation2=setup_attempt(f,publication)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt2,reservation2)
    svc2.claim(reservation2)
    svc2.fail(
        reservation2,bytes_processed=None,bytes_billed=None,
        provider_job_id="synthetic-failed-job",duration_ms=1,
    )
    with f.pg["connect"]() as conn:
        assert dependency(conn,f.resource,publication["publication_id"]) is False


def test_cross_attempt_token_cannot_bind_reservation(linked_pg):
    f,publication=linked_pg
    svc,attempt,reservation=setup_attempt(f,publication,token="f"*64)
    with pytest.raises(psycopg.Error,match="Matching owned ACTIVE SQL reservation required"):
        with f.pg["connect"]() as conn:
            insert_link(conn,attempt,reservation)
    with f.pg["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_reservation_links"
        ).fetchone()[0]==0


def test_no_pins_cannot_bind_reservation(linked_pg):
    f,publication=linked_pg
    svc,attempt,reservation=setup_attempt(f,publication,pin=False)
    with pytest.raises(psycopg.Error,match="One or two pinned publications required"):
        with f.pg["connect"]() as conn:
            insert_link(conn,attempt,reservation)


def test_stale_head_cannot_bind_preexisting_historical_pin(linked_pg):
    f,publication=linked_pg
    svc,attempt,reservation=setup_attempt(f,publication)
    manifests=registered(f)
    next_publication=execute(
        f,intent(f,manifests,kind="INCREMENTAL",key="new-head"),manifests,
    )
    assert next_publication["publication_id"]!=publication["publication_id"]
    with pytest.raises(psycopg.Error,match="Current publication pin set required"):
        with f.pg["connect"]() as conn:
            insert_link(conn,attempt,reservation)


def test_link_and_linked_pin_set_are_immutable(linked_pg):
    f,publication=linked_pg
    svc,attempt,reservation=setup_attempt(f,publication)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    with pytest.raises(psycopg.Error,match="immutable"):
        with f.pg["connect"]() as conn:
            conn.execute(
                "DELETE FROM warehouse_query_attempt_reservation_links WHERE attempt_id=%s",
                (attempt.attempt_id,),
            )
    with pytest.raises(psycopg.Error,match="immutable"):
        with f.pg["connect"]() as conn:
            conn.execute(
                "UPDATE warehouse_query_attempt_reservation_links "
                "SET reservation_id=reservation_id WHERE attempt_id=%s",
                (attempt.attempt_id,),
            )
    with pytest.raises(psycopg.Error,match="Linked publication pin set is immutable"):
        with f.pg["connect"]() as conn:
            conn.execute(
                """INSERT INTO warehouse_query_attempt_publication_pins
                (attempt_id,resource_id,publication_id,publication_revision)
                VALUES (%s,%s,%s,%s)""",
                (attempt.attempt_id,f.resource,publication["publication_id"],
                 publication["revision"]),
            )


def test_dependency_view_is_non_authorizing_read_only():
    sql=(ROOT/"migrations/0050_warehouse_query_reservation_publication_links.sql").read_text(
        encoding="utf-8",
    )
    assert "RETURNS BOOLEAN LANGUAGE sql STABLE" in sql
    assert "r.status='EXECUTING'" in sql
    assert "r.status='ACTIVE'" in sql
    assert "FOR SHARE OF h" in sql
    assert "Current publication pin set required" in sql
    assert "DELETE FROM warehouse_publications" not in sql
