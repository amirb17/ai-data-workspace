"""414C2: real published-head pinning and PostgreSQL concurrency fences.

Uses the existing production-equivalent reliability publication service and
isolated PostgreSQL schema; provider SQL is never executed.
"""
from pathlib import Path

import psycopg
import pytest

from app.services.warehouse_query_attempt_service import create_or_resume_warehouse_attempt
from tests.test_warehouse_reliability import (
    reliability_pg, registered, intent, execute, publish_source,
)
from tests.test_warehouse_sql_admission_postgres import service

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def published_query_pg(reliability_pg):
    f=reliability_pg
    with f.pg["connect"]() as conn:
        for name in (
            "0048_warehouse_query_attempt_registry.sql",
            "0049_warehouse_query_attempt_publication_pins.sql",
        ):
            conn.execute((ROOT/"migrations"/name).read_text(encoding="utf-8"))
    first=registered(f)
    publication=execute(f,intent(f,first),first)
    return f,publication


def new_attempt(f,digest="a"):
    return create_or_resume_warehouse_attempt(
        governance=service(f.pg),logical_intent_sha256=digest*64,
    )


def insert_pin(conn,attempt,resource,publication):
    conn.execute(
        """INSERT INTO warehouse_query_attempt_publication_pins
           (attempt_id,resource_id,publication_id,publication_revision)
           VALUES (%s,%s,%s,%s)""",
        (attempt.attempt_id,resource,publication["publication_id"],
         publication["revision"]),
    )


def test_current_published_head_can_be_pinned_without_query_reservation(
    published_query_pg,
):
    f,publication=published_query_pg
    attempt=new_attempt(f)
    with f.pg["connect"]() as conn:
        insert_pin(conn,attempt,f.resource,publication)
        persisted=conn.execute(
            """SELECT publication_id,publication_revision FROM
               warehouse_query_attempt_publication_pins WHERE attempt_id=%s""",
            (attempt.attempt_id,),
        ).fetchone()
        assert persisted==(publication["publication_id"],publication["revision"])
        assert conn.execute(
            "SELECT count(*) FROM query_governance_reservations"
        ).fetchone()[0]==0


def test_stale_publication_cannot_be_newly_pinned_after_head_advances(
    published_query_pg,
):
    f,first=published_query_pg
    next_manifest=registered(f)
    second=execute(f,intent(f,next_manifest,kind="INCREMENTAL",key="next"),
                   next_manifest)
    assert second["revision"]==first["revision"]+1
    assert second["publication_id"]!=first["publication_id"]
    attempt=new_attempt(f)
    with pytest.raises(psycopg.Error,match="Current warehouse publication head required"):
        with f.pg["connect"]() as conn:
            insert_pin(conn,attempt,f.resource,first)
    with f.pg["connect"]() as conn:
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_attempt_publication_pins"
        ).fetchone()[0]==0


def test_existing_immutable_pin_survives_head_advance_but_not_new_authorization(
    published_query_pg,
):
    f,first=published_query_pg
    prior=new_attempt(f)
    with f.pg["connect"]() as conn:
        insert_pin(conn,prior,f.resource,first)
    next_manifest=registered(f)
    second=execute(f,intent(f,next_manifest,kind="INCREMENTAL",key="advance"),
                   next_manifest)
    later=new_attempt(f,"b")
    with pytest.raises(psycopg.Error,match="Current warehouse publication head required"):
        with f.pg["connect"]() as conn:
            insert_pin(conn,later,f.resource,first)
    with f.pg["connect"]() as conn:
        row=conn.execute(
            """SELECT publication_id FROM warehouse_query_attempt_publication_pins
               WHERE attempt_id=%s""",
            (prior.attempt_id,),
        ).fetchone()
        assert row==(first["publication_id"],)
        assert second["publication_id"]!=first["publication_id"]
        # Historical pin retention is metadata-only, not physical table retention.


def test_head_update_blocked_while_pin_insert_transaction_is_uncommitted(
    published_query_pg,
):
    f,publication=published_query_pg
    attempt=new_attempt(f)
    # A separate writer cannot change head while the pin insertion holds its
    # FOR SHARE lock. A bounded lock_timeout prevents any hanging test.
    with f.pg["connect"]() as pin_conn:
        insert_pin(pin_conn,attempt,f.resource,publication)
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with f.pg["connect"]() as writer:
                writer.execute("SET LOCAL lock_timeout = '300ms'")
                writer.execute(
                    """UPDATE warehouse_publication_heads SET
                       publication_id=publication_id WHERE resource_id=%s""",
                    (f.resource,),
                )
    with f.pg["connect"]() as conn:
        assert conn.execute(
            """SELECT count(*) FROM warehouse_query_attempt_publication_pins
               WHERE attempt_id=%s""",
            (attempt.attempt_id,),
        ).fetchone()[0]==1


def test_pin_immutability_and_per_attempt_uniqueness_on_published_resource(
    published_query_pg,
):
    f,publication=published_query_pg
    attempt=new_attempt(f)
    with f.pg["connect"]() as conn:
        insert_pin(conn,attempt,f.resource,publication)
    with pytest.raises(psycopg.Error,match="immutable"):
        with f.pg["connect"]() as conn:
            conn.execute(
                """UPDATE warehouse_query_attempt_publication_pins
                   SET publication_revision=publication_revision
                   WHERE attempt_id=%s""",
                (attempt.attempt_id,),
            )
    with pytest.raises(psycopg.Error,match="immutable"):
        with f.pg["connect"]() as conn:
            conn.execute(
                "DELETE FROM warehouse_query_attempt_publication_pins WHERE attempt_id=%s",
                (attempt.attempt_id,),
            )
    with pytest.raises(psycopg.errors.UniqueViolation):
        with f.pg["connect"]() as conn:
            insert_pin(conn,attempt,f.resource,publication)
