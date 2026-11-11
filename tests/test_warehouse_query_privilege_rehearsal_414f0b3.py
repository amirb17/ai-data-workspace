"""414F0-B3: transactional PostgreSQL privilege checks on an explicitly disposable DB.

Never runs using DATABASE_URL or RUN_DB_TESTS. The distinct B3 URL must name
a database starting datarise_b3_test_. All role/schema DDL rolls back.
No cloud IO, persistent grants, or production migrations.
"""
import os
import re
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from app.security.warehouse_privilege_assessment import ROLES, TABLES

GATE = "RUN_B3_ISOLATED_DB_TESTS"
URL = "B3_DISPOSABLE_DATABASE_URL"
PREFIX = "datarise_b3_test_"


def validate_b3_target(enabled, dsn, application_dsn=None):
    if enabled != "1":
        return False
    if not dsn:
        raise ValueError("B3 requires an explicit disposable database URL")
    try:
        target = conninfo_to_dict(dsn)
        app = conninfo_to_dict(application_dsn) if application_dsn else None
    except psycopg.Error as exc:
        raise ValueError("Malformed B3 database connection info") from exc
    dbname = target.get("dbname", "")
    if not re.fullmatch(r"datarise_b3_test_[a-z0-9_]+", dbname):
        raise ValueError("B3 requires a dedicated datarise_b3_test_* database")
    if app and (
        target.get("dbname") == app.get("dbname")
        and (target.get("host") or "localhost") == (app.get("host") or "localhost")
        and (target.get("port") or "5432") == (app.get("port") or "5432")
    ):
        raise ValueError("B3 may not target the application database")
    return True


def test_gate_is_off_by_default(monkeypatch):
    monkeypatch.delenv(GATE, raising=False)
    assert validate_b3_target(os.getenv(GATE), None) is False


@pytest.mark.parametrize("database", ["postgres", "template1", "datarise", "public", "datarise_b3_test_"])
def test_reject_non_disposable_database_name(database):
    with pytest.raises(ValueError, match="dedicated"):
        validate_b3_target("1", f"dbname={database}")


def test_reject_same_application_database():
    dsn = "dbname=datarise_b3_test_isolated host=127.0.0.1 port=5432"
    with pytest.raises(ValueError, match="application database"):
        validate_b3_target("1", dsn, dsn)


def test_expected_roles_and_metadata_scope_are_stable():
    assert len(ROLES) == 5
    assert len(TABLES) == 9
    assert "datarise_authority_owner" in ROLES
    assert "warehouse_query_send_journal" in TABLES


def _assert_denied(conn, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        # A nested savepoint keeps the outer DDL transaction healthy.
        with conn.transaction():
            conn.execute(statement)


def test_disposable_role_privileges_under_postgres_transaction():
    if os.getenv(GATE) != "1":
        pytest.skip("Explicit isolated B3 database gate is not enabled")
    dsn = os.getenv(URL)
    from app.db import database
    validate_b3_target("1", dsn, database.DATABASE_URL)
    suffix = uuid.uuid4().hex[:16]
    schema = "b3_" + suffix
    readers = ["b3_api_" + suffix, "b3_worker_" + suffix, "b3_verifier_" + suffix]
    # No passwords or LOGIN: these are only test SET ROLE simulations.
    with psycopg.connect(dsn) as conn:
        try:
            assert conn.execute("SELECT current_database()").fetchone()[0].startswith(PREFIX)
            assert conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]
            for name in readers:
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(sql.Identifier(name)))
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("CREATE TABLE {}.readonly_control (id int primary key)").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("CREATE TABLE {}.dispatch_control (id int primary key)").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("INSERT INTO {}.readonly_control VALUES (1)").format(sql.Identifier(schema)))
            for name in readers:
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(name)))
            conn.execute(sql.SQL("GRANT SELECT ON {}.readonly_control TO {}").format(
                sql.Identifier(schema), sql.Identifier(readers[0])))
            conn.execute(sql.SQL("GRANT SELECT ON {}.dispatch_control TO {}").format(
                sql.Identifier(schema), sql.Identifier(readers[2])))
            read = sql.SQL("SELECT id FROM {}.readonly_control").format(sql.Identifier(schema))
            write = sql.SQL("INSERT INTO {}.readonly_control (id) VALUES (2)").format(sql.Identifier(schema))
            erase = sql.SQL("DELETE FROM {}.readonly_control").format(sql.Identifier(schema))
            dispatch = sql.SQL("SELECT id FROM {}.dispatch_control").format(sql.Identifier(schema))
            for index, role in enumerate(readers):
                conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                try:
                    assert conn.execute("SELECT current_user").fetchone()[0] == role
                    if index == 0:
                        assert conn.execute(read).fetchall() == [(1,)]
                        _assert_denied(conn, dispatch)
                    elif index == 2:
                        assert conn.execute(dispatch).fetchall() == []
                        _assert_denied(conn, read)
                    else:
                        _assert_denied(conn, read)
                        _assert_denied(conn, dispatch)
                    _assert_denied(conn, write)
                    _assert_denied(conn, erase)
                    _assert_denied(conn, sql.SQL("TRUNCATE {}.readonly_control").format(sql.Identifier(schema)))
                    _assert_denied(conn, sql.SQL("CREATE TABLE {}.forbidden (id int)").format(sql.Identifier(schema)))
                finally:
                    conn.execute("RESET ROLE")
        finally:
            # Transactional CREATE ROLE/SCHEMA/GRANT never survive this rollback.
            conn.rollback()
