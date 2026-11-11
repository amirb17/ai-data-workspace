"""414F0-B11 - actual migrated function ACL isolation, rollback-only.

Runs exclusively inside the existing disposable 414E3 warehouse fixture.
Temporary NOLOGIN roles, REVOKE and GRANT changes roll back on exit.
Checks effective EXECUTE privilege via PostgreSQL, never invokes functions
or turns on BigQuery query submission.
"""
import os
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.security.warehouse_catalog_assessment import EXPECTED_FUNCTIONS
from tests.test_warehouse_query_function_trigger_integration_414f0b7 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, linked_pg,
    reliability_pg, registered, intent, execute,
)


def _functions(conn, schema):
    found = conn.execute(
        """SELECT p.proname, p.oid, p.oid::regprocedure::text, p.prosecdef,
                  p.proconfig, pg_get_userbyid(p.proowner)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname=%s AND p.proname=ANY(%s)
           ORDER BY p.proname""",
        (schema, sorted(EXPECTED_FUNCTIONS)),
    ).fetchall()
    assert len(found) == len(EXPECTED_FUNCTIONS)
    assert {r[0] for r in found} == EXPECTED_FUNCTIONS
    return found


def test_real_function_security_metadata_is_complete(unbound):
    s, _, client = unbound
    with s.f.pg["connect"]() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        assert schema.startswith("test_warehouse_") and schema != "public"
        found = _functions(conn, schema)
        for name, oid, signature, security_definer, settings, owner in found:
            assert not security_definer, name
            assert owner and isinstance(signature, str), name
            assert settings and any(item.startswith("search_path=") for item in settings), name
        conn.rollback()
    assert not client.calls


@pytest.mark.parametrize("chosen_name", sorted(EXPECTED_FUNCTIONS))
def test_function_acl_is_grantable_to_only_one_ephemeral_role(unbound, chosen_name):
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Explicit warehouse fixture requires RUN_DB_TESTS=1")
    s, _, client = unbound
    with s.f.pg["connect"]() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        assert schema.startswith("test_warehouse_") and schema != "public"
        assert conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()[0]
        found = _functions(conn, schema)
        target = next(r for r in found if r[0] == chosen_name)
        role = "b11_" + uuid4().hex[:20]
        try:
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(sql.Identifier(role)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)))
            for name, oid, signature, secdef, settings, owner in found:
                # The test is rollback-only. In actual deployment, grants
                # must be reviewed across all dependent functions.
                conn.execute(sql.SQL("REVOKE EXECUTE ON FUNCTION {} FROM PUBLIC").format(
                    sql.SQL(signature)))
            conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                sql.SQL(target[2]), sql.Identifier(role)))
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            try:
                assert conn.execute("SELECT current_user").fetchone()[0] == role
                for name, oid, signature, *_ in found:
                    allowed = conn.execute(
                        "SELECT has_function_privilege(current_user,%s,'EXECUTE')", (oid,)
                    ).fetchone()[0]
                    assert allowed is (name == chosen_name), (chosen_name, name, allowed)
                assert conn.execute(
                    "SELECT has_schema_privilege(current_user,%s,'CREATE')", (schema,)
                ).fetchone()[0] is False
            finally:
                conn.execute("RESET ROLE")
        finally:
            conn.rollback()
        assert conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
        ).fetchone() is None
        conn.rollback()
    assert not client.calls
