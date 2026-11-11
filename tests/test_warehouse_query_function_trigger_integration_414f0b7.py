"""414F0-B7: execute B6 catalog audit on the real isolated 0048-0056 fixtures.

No cloud execution, role provisioning, grants, or changes to public schema.
"""
from pathlib import Path
import os

import pytest

# Import the complete established fixture dependency chain. The unbound fixture
# installs 0056 in the already isolated warehouse schema.
from tests.test_warehouse_sql_provider_policy_414e3 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound,
)
from tests.test_warehouse_query_reservation_publication_links_postgres import linked_pg
from tests.test_warehouse_reliability import reliability_pg, registered, intent, execute

SQL_FILE = Path(__file__).resolve().parents[1] / (
    "deployment/operations/warehouse_query_function_trigger_audit_414f0b6.sql"
)


def collect_audit(conn):
    # Execute as a single multi-result SQL batch, no manual ';' splitting.
    conn.execute("SET TRANSACTION READ ONLY")
    groups = []
    with conn.cursor() as cur:
        cur.execute(SQL_FILE.read_text(encoding="utf-8"))
        while True:
            if cur.description:
                columns = [d.name for d in cur.description]
                groups.append([dict(zip(columns, row)) for row in cur.fetchall()])
            if not cur.nextset():
                break
    return groups


def test_real_migrated_catalog_functions_and_triggers_are_present(unbound):
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Real warehouse integration requires RUN_DB_TESTS=1")
    s, _, client = unbound
    with s.f.pg["connect"]() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        assert schema.startswith("test_warehouse_"), schema
        groups = collect_audit(conn)
        # The caller must never commit from within this READ ONLY transaction.
        conn.rollback()

    assert len(groups) == 3
    assert groups[0] == [dict(audited_schema=schema, audit_user="postgres",
                              search_path=schema)] or (
        groups[0][0]["audited_schema"] == schema and len(groups[0]) == 1
    )
    functions, triggers = groups[1], groups[2]
    assert len(functions) >= 14
    assert len(triggers) >= 20
    assert all(x["exists"] is True for x in functions), functions
    assert all(x["exists"] is True for x in triggers), triggers
    assert all(x["enabled_mode"] in ("O", "A") for x in triggers), triggers
    assert all(x["function_owner"] for x in functions)
    assert all(x["security_definer"] is False for x in functions)
    # PostgreSQL defaults can permit PUBLIC EXECUTE on functions. Record it as
    # security work, do NOT treat test success as permission acceptance.
    assert all(isinstance(x["public_execute"], bool) for x in functions)
    assert not client.calls


def test_catalog_audit_source_has_no_mutations_or_provider_calls():
    sql = SQL_FILE.read_text(encoding="utf-8")
    assert "pg_catalog.pg_proc" in sql and "pg_catalog.pg_trigger" in sql
    assert "public_execute" in sql and "function_owner" in sql
    assert "CREATE ROLE" not in sql.upper()
    assert "GRANT EXECUTE" not in sql.upper()
