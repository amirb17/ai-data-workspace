"""414F0-B9: privilege exercise on ACTUAL 0053-0056 migrated control tables.

Temporary NOLOGIN roles/GRANTs are created and rolled back in an already
disposable PostgreSQL fixture schema. This is not a credential/login test,
runtime grant specification, or permission to enable BigQuery dispatch.
"""
import os
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from tests.test_warehouse_query_function_trigger_integration_414f0b7 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, linked_pg,
    reliability_pg, registered, intent, execute,
)

# Existing tables created by the fully composed E3 warehouse fixture.
# A single SELECT is deliberately allowed to each role on ONE real control table.
READ_TABLES = {
    "api": "warehouse_query_provider_policies",
    "worker": "warehouse_query_send_journal",
    "verifier": "warehouse_query_recovery_attestations",
    "dispatch": "warehouse_query_dispatch_checkpoints",
}
PROTECTED = tuple(READ_TABLES.values())


def _denied(conn, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():  # savepoint only; outer role DDL is still live
            conn.execute(statement)


@pytest.mark.parametrize("reader", sorted(READ_TABLES))
def test_real_warehouse_table_permissions_are_restrictive(unbound, reader):
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Explicit isolated PostgreSQL schema tests require RUN_DB_TESTS=1")
    s, _, client = unbound
    with s.f.pg["connect"]() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        assert schema.startswith("test_warehouse_") and schema != "public"
        assert conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()[0], "Disposable role DDL requires the isolated test superuser"

        role = f"b9_{reader}_{uuid4().hex[:12]}"
        own_table = READ_TABLES[reader]
        try:
            # All temporary cluster-global objects are held in one uncommitted
            # transaction. Unlike production roles these are NOLOGIN identities.
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(
                sql.Identifier(role)
            ))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            ))
            conn.execute(sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                sql.Identifier(schema), sql.Identifier(own_table),
                sql.Identifier(role)
            ))

            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            try:
                assert conn.execute("SELECT current_user").fetchone()[0] == role
                assert conn.execute(
                    sql.SQL("SELECT count(*) FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(own_table)
                    )
                ).fetchone()[0] >= 0

                for other in PROTECTED:
                    if other != own_table:
                        _denied(conn, sql.SQL("SELECT count(*) FROM {}.{}").format(
                            sql.Identifier(schema), sql.Identifier(other)
                        ))

                qualified = sql.SQL("{}.{}").format(
                    sql.Identifier(schema), sql.Identifier(own_table)
                )
                _denied(conn, sql.SQL("INSERT INTO {} DEFAULT VALUES").format(qualified))
                _denied(conn, sql.SQL("UPDATE {} SET attempt_id=attempt_id WHERE FALSE").format(qualified))
                _denied(conn, sql.SQL("DELETE FROM {} WHERE FALSE").format(qualified))
                _denied(conn, sql.SQL("TRUNCATE {}").format(qualified))
                _denied(conn, sql.SQL("SELECT * FROM {} FOR UPDATE").format(qualified))
                _denied(conn, sql.SQL("CREATE TABLE {}.forbidden (id int)").format(
                    sql.Identifier(schema)
                ))
            finally:
                conn.execute("RESET ROLE")
        finally:
            # Rolled back rather than committed: no persistent role or grant.
            conn.rollback()
        # Verify the temporary cluster role was actually removed by rollback.
        assert conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone() is None
        conn.rollback()
    assert not client.calls


def test_real_table_rehearsal_never_provisions_persistent_roles():
    import inspect
    source = inspect.getsource(test_real_warehouse_table_permissions_are_restrictive)
    assert "CREATE ROLE" in source and "NOLOGIN NOINHERIT" in source
    assert 'conn.rollback()' in source
    assert "RESET ROLE" in source
    assert set(READ_TABLES) == {"api", "worker", "verifier", "dispatch"}
