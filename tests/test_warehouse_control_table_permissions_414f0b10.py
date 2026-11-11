"""414F0-B10: all nine real warehouse-control tables, negative DML + role scope.

Uses an existing isolated migrated warehouse schema. All test roles/grants
are transaction-scoped and rolled back; no runtime credentials or provider IO.
This is a security regression, not production permission provisioning.
"""
import os
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.security.warehouse_privilege_assessment import TABLES
from tests.test_warehouse_query_function_trigger_integration_414f0b7 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, linked_pg,
    reliability_pg, registered, intent, execute,
)


def _reject(conn, statement):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute(statement)


def _full_table_inventory(conn, schema):
    return {
        name: (relkind, owner)
        for name, relkind, owner in conn.execute(
            """SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner)
            FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace
            WHERE ns.nspname=%s AND c.relname=ANY(%s)""",
            (schema, sorted(TABLES)),
        )
    }


def test_all_nine_migrated_control_relations_exist_and_are_not_publicly_writable(unbound):
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Requires RUN_DB_TESTS=1 and isolated warehouse PostgreSQL fixture")
    s, _, client = unbound
    with s.f.pg["connect"]() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        assert schema.startswith("test_warehouse_") and schema != "public"
        observed = _full_table_inventory(conn, schema)
        assert set(observed) == TABLES, (sorted(TABLES - observed.keys()), sorted(observed.keys() - TABLES))
        assert all(kind in ("r", "p") and owner for kind, owner in observed.values())
        # PostgreSQL may have inherited/default ACLs. Check effective access
        # for the PUBLIC pseudo-role using catalog ACLs rather than table-level
        # rights of the superuser fixture connection.
        rows = conn.execute(
            """SELECT c.relname, COALESCE(
                bool_or(a.grantee=0 AND a.privilege_type IN
                    ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')),false
            ) AS public_mutation
            FROM pg_class c
            JOIN pg_namespace ns ON ns.oid=c.relnamespace
            LEFT JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner)))
                a ON true
            WHERE ns.nspname=%s AND c.relname=ANY(%s)
            GROUP BY c.relname""",
            (schema, sorted(TABLES)),
        ).fetchall()
        assert len(rows) == len(TABLES)
        assert not any(public_mutation for _, public_mutation in rows), rows
        conn.rollback()
    assert not client.calls


@pytest.mark.parametrize("table", sorted(TABLES))
def test_each_real_table_only_exposes_explicit_select_to_ephemeral_role(unbound, table):
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Requires RUN_DB_TESTS=1 and isolated warehouse PostgreSQL fixture")
    s, _, client = unbound
    with s.f.pg["connect"]() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        assert schema.startswith("test_warehouse_") and schema != "public"
        role = "b10_" + uuid4().hex[:20]
        first_column = conn.execute(
            """SELECT a.attname FROM pg_attribute a
            JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0
              AND NOT a.attisdropped
            ORDER BY a.attnum LIMIT 1""",
            (schema, table),
        ).fetchone()[0]
        assert conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()[0]
        try:
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(sql.Identifier(role)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)))
            conn.execute(sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                sql.Identifier(schema), sql.Identifier(table), sql.Identifier(role)))
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            try:
                assert conn.execute("SELECT current_user").fetchone()[0] == role
                q = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
                assert conn.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(q)).fetchone()[0] >= 0
                for other in sorted(TABLES - {table}):
                    _reject(conn, sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(other)))
                # Select on a table must not include row-locking, mutating or
                # trigger privileges. WHERE FALSE avoids touching fixture rows.
                _reject(conn, sql.SQL("SELECT * FROM {} FOR UPDATE").format(q))
                _reject(conn, sql.SQL("UPDATE {} SET {}={} WHERE FALSE").format(
                    q, sql.Identifier(first_column), sql.Identifier(first_column)))
                _reject(conn, sql.SQL("DELETE FROM {} WHERE FALSE").format(q))
                _reject(conn, sql.SQL("TRUNCATE {}").format(q))
                _reject(conn, sql.SQL("INSERT INTO {} DEFAULT VALUES").format(q))
                _reject(conn, sql.SQL("CREATE TABLE {}.unapproved (id int)").format(
                    sql.Identifier(schema)))
            finally:
                conn.execute("RESET ROLE")
        finally:
            conn.rollback()
        assert conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone() is None
        conn.rollback()
    assert not client.calls
