"""414C3b: metadata retirement fence, serialized with query link creation.

All tests operate in isolated PostgreSQL schemas. No provider deletion.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_reliability import reliability_pg, registered, intent, execute
from tests.test_warehouse_query_reservation_publication_links_postgres import (
    linked_pg, setup_attempt, insert_link, dependency,
)

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def retirement_pg(linked_pg):
    f,first=linked_pg
    with f.pg["connect"]() as conn:
        conn.execute(
            (ROOT/"migrations/0051_warehouse_query_publication_retirement_fences.sql")
            .read_text(encoding="utf-8")
        )
    return f,first


def advance(f,label):
    manifests=registered(f)
    return execute(
        f,intent(f,manifests,kind="INCREMENTAL",key=label),manifests,
    )


def fence(conn,resource,publication_id):
    conn.execute(
        """INSERT INTO warehouse_query_publication_retirement_fences
           (resource_id,publication_id) VALUES (%s,%s)""",
        (resource,publication_id),
    )


def test_current_publication_cannot_be_retirement_fenced(retirement_pg):
    f,current=retirement_pg
    with pytest.raises(psycopg.Error,match="Current publication cannot be retirement fenced"):
        with f.pg["connect"]() as conn:
            fence(conn,f.resource,current["publication_id"])


def test_active_and_executing_queries_block_retirement(retirement_pg):
    f,old=retirement_pg
    svc,attempt,reservation=setup_attempt(f,old)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    advance(f,"head-after-active-link")
    with pytest.raises(psycopg.Error,match="Active query publication dependency blocks retirement"):
        with f.pg["connect"]() as conn:
            fence(conn,f.resource,old["publication_id"])

    svc.claim(reservation)
    with pytest.raises(psycopg.Error,match="Active query publication dependency blocks retirement"):
        with f.pg["connect"]() as conn:
            fence(conn,f.resource,old["publication_id"])


def test_released_dependency_allows_metadata_retirement_fence(retirement_pg):
    f,old=retirement_pg
    svc,attempt,reservation=setup_attempt(f,old)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    advance(f,"head-after-release")
    svc.release(reservation)
    with f.pg["connect"]() as conn:
        assert dependency(conn,f.resource,old["publication_id"]) is False
        fence(conn,f.resource,old["publication_id"])
        assert conn.execute(
            "SELECT count(*) FROM warehouse_query_publication_retirement_fences"
        ).fetchone()[0]==1
    with pytest.raises(psycopg.Error,match="immutable"):
        with f.pg["connect"]() as conn:
            conn.execute(
                """DELETE FROM warehouse_query_publication_retirement_fences
                WHERE resource_id=%s AND publication_id=%s""",
                (f.resource,old["publication_id"]),
            )


def test_retirement_and_link_creation_are_serialized(retirement_pg):
    f,old=retirement_pg
    # An ACTIVE reservation + old pin that has not yet been linked does
    # not count as a dependency; the competing link cannot be admitted
    # after retirement has committed.
    svc,attempt,reservation=setup_attempt(f,old)
    advance(f,"head-after-unlinked-pin")

    with f.pg["connect"]() as retiring:
        fence(retiring,f.resource,old["publication_id"])
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with f.pg["connect"]() as competing:
                competing.execute("SET LOCAL lock_timeout = '300ms'")
                insert_link(competing,attempt,reservation)

    with pytest.raises(psycopg.Error,match="Retired publication cannot acquire query dependency"):
        with f.pg["connect"]() as conn:
            insert_link(conn,attempt,reservation)


def test_fence_is_metadata_only_and_creates_no_provider_execution():
    sql=(ROOT/"migrations/0051_warehouse_query_publication_retirement_fences.sql").read_text(
        encoding="utf-8",
    )
    assert "pg_advisory_xact_lock" in sql
    assert "warehouse_query_publication_dependency_present" in sql
    assert "a_warehouse_query_retirement_link_validate" in sql
    assert "Retired publication cannot acquire query dependency" in sql
    assert "delete_table" not in sql
    assert "DROP TABLE" not in sql
