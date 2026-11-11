"""414F0-B restricted-login acceptance, explicitly opt-in disposable PG only.

No role creation, grants or irreversible DML. The caller supplies a
reviewed, isolated schema and separate role credentials at test time.
"""
import os
import re

import psycopg
import pytest
from psycopg import sql

from app.warehouse.pg_result_reader_permission_probe import PROTECTED_TABLES

_GATE="RUN_RESTRICTED_DB_ACCEPTANCE"
_DSN="RESTRICTED_DB_ACCEPTANCE_DSN"
_ROLE="RESTRICTED_DB_EXPECTED_ROLE"
_SCHEMA="RESTRICTED_DB_ACCEPTANCE_SCHEMA"


def _settings():
    if os.getenv(_GATE)!="1":
        pytest.skip("Restricted PostgreSQL acceptance explicitly disabled")
    dsn=os.getenv(_DSN)
    role=os.getenv(_ROLE)
    schema=os.getenv(_SCHEMA)
    if (not dsn or not role or not schema
            or not re.fullmatch(r"datarise_[a-z][a-z0-9_]{2,62}",role)
            or not re.fullmatch(r"test_warehouse_[a-z0-9_]{1,48}",schema)):
        pytest.fail("Restricted acceptance requires reviewed DSN, role and disposable test_warehouse_ schema")
    return dsn,role,schema


@pytest.fixture(scope="module")
def restricted_connection():
    dsn,role,schema=_settings()
    # No credential material is echoed. Never fall back to developer DSN.
    try:
        conn=psycopg.connect(dsn,connect_timeout=5,autocommit=False)
    except psycopg.Error:
        pytest.fail("Restricted login unavailable (connection details redacted)")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT session_user,current_user")
            session_user,current_user=cur.fetchone()
            assert (session_user,current_user)==(role,role), (
                "ACTUAL_DIRECT_RESTRICTED_LOGIN_REQUIRED"
            )
            cur.execute("SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            flags=cur.fetchone()
            assert flags is not None and flags[0] and not any(flags[1:]), (
                "RESTRICTED_ROLE_ESCALATION_DETECTED"
            )
            cur.execute("SELECT has_schema_privilege(current_user,%s,'CREATE')",(schema,))
            assert cur.fetchone()[0] is False, "RESTRICTED_ROLE_SCHEMA_CREATE_NOT_ALLOWED"
            cur.execute("""
                SELECT c.relname
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=%s AND c.relname=ANY(%s)
                  AND c.relkind IN ('r','p')
            """,(schema,list(PROTECTED_TABLES)))
            existing={row[0] for row in cur.fetchall()}
            assert existing==set(PROTECTED_TABLES), "FULL_DISPOSABLE_WAREHOUSE_CATALOG_REQUIRED"
        conn.commit()
        yield conn,schema
    finally:
        conn.rollback()
        conn.close()


def test_restricted_login_has_no_protected_warehouse_table_mutation(restricted_connection):
    conn,schema=restricted_connection
    with conn.cursor() as cur:
        for table in PROTECTED_TABLES:
            qualified=sql.Identifier(schema,table)
            # Actual authorization check, but EXPLAIN without ANALYZE
            # executes no DML and never modifies fixture rows.
            statement=sql.SQL("EXPLAIN DELETE FROM {} WHERE FALSE").format(qualified)
            try:
                with conn.transaction():
                    cur.execute(statement)
            except psycopg.errors.InsufficientPrivilege:
                continue
            except psycopg.Error:
                pytest.fail(f"Unexpected SQL permission diagnostic for protected table {table}")
            pytest.fail(f"Restricted role can DELETE protected warehouse table {table}")


def test_restricted_login_metadata_privileges_are_deny_only(restricted_connection):
    conn,schema=restricted_connection
    with conn.cursor() as cur:
        for table in PROTECTED_TABLES:
            cur.execute("""
                SELECT has_table_privilege(current_user,c.oid,'SELECT'),
                       has_table_privilege(current_user,c.oid,'INSERT'),
                       has_table_privilege(current_user,c.oid,'UPDATE'),
                       has_table_privilege(current_user,c.oid,'DELETE'),
                       has_table_privilege(current_user,c.oid,'TRUNCATE'),
                       has_table_privilege(current_user,c.oid,'REFERENCES'),
                       has_table_privilege(current_user,c.oid,'TRIGGER')
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname=%s AND c.relname=%s
            """,(schema,table))
            rights=cur.fetchone()
            assert rights is not None, f"Protected table missing: {table}"
            assert not any(rights), f"Protected table privilege must be denied: {table}"
