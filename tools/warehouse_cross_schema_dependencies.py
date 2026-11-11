"""Inspect exact warehouse query foreign-key and trigger dependencies.

Read-only PostgreSQL catalog audit for the dual-schema deployment. Identifies
which isolated query objects bind to isolated copies of application tables,
including indirectly through trigger functions, before any cross-schema DDL.

No SQL migrations, GRANT/REVOKE, session data, credentials, provider IO or
runtime enablement. Output has *schema/object names only*, no user records.

Usage:
    python -m tools.warehouse_cross_schema_dependencies
"""
from __future__ import annotations

import json
import sys

import psycopg

from tools.warehouse_deployment_boundary import _dsn

SCHEMA = "datarise_warehouse_dev"
CANONICAL = "public"
IDENTITY_TABLES = frozenset({
    "users", "auth_sessions", "workspaces", "datasets",
    "warehouse_models", "warehouse_model_versions",
    "warehouse_model_sources", "warehouse_model_configurations",
    "warehouse_resources", "warehouse_publications",
    "warehouse_publication_heads", "warehouse_serving_materializations",
    "query_governance_reservations", "query_governance_usage",
})
QUERY_PREFIX = "warehouse_query_"


def inspect(conn) -> dict:
    database = conn.execute("SELECT current_database()").fetchone()[0]
    if database != "ai_data_workspace":
        raise RuntimeError("UNAPPROVED_DATABASE")

    fk_rows = conn.execute("""
        SELECT n.nspname, t.relname, c.conname,
               rn.nspname, rt.relname,
               pg_get_constraintdef(c.oid, true)
          FROM pg_catalog.pg_constraint c
          JOIN pg_catalog.pg_class t ON t.oid=c.conrelid
          JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
          JOIN pg_catalog.pg_class rt ON rt.oid=c.confrelid
          JOIN pg_catalog.pg_namespace rn ON rn.oid=rt.relnamespace
         WHERE c.contype='f' AND n.nspname=%s
           AND t.relname LIKE 'warehouse_query_%%'
         ORDER BY t.relname,c.conname
    """, (SCHEMA,)).fetchall()
    fks = [{
        "table": table, "constraint": constraint,
        "referenced_schema": target_schema,
        "referenced_table": target,
        "binds_isolated_canonical_copy":
            target_schema == SCHEMA and target in IDENTITY_TABLES,
    } for _, table, constraint, target_schema, target, _definition in fk_rows]

    triggers = conn.execute("""
        SELECT t.relname, tr.tgname, p.proname,
               pn.nspname, p.prosecdef,
               pg_get_functiondef(p.oid)
          FROM pg_catalog.pg_trigger tr
          JOIN pg_catalog.pg_class t ON t.oid=tr.tgrelid
          JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
          JOIN pg_catalog.pg_proc p ON p.oid=tr.tgfoid
          JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
         WHERE n.nspname=%s AND NOT tr.tgisinternal
           AND (t.relname LIKE 'warehouse_query_%%'
                OR p.proname LIKE 'warehouse_query_%%')
         ORDER BY t.relname,tr.tgname
    """, (SCHEMA,)).fetchall()
    trigger_items = []
    for table, name, func, func_schema, definer, definition in triggers:
        # A trigger is flagged whenever it references canonical table names
        # without an explicit schema. This is a conservative *review signal*,
        # not a parser or automatic rewrite authorization.
        unqualified = sorted(
            tbl for tbl in IDENTITY_TABLES
            if __import__("re").search(
                r"(?i)(?<![\\w.])" + __import__("re").escape(tbl) + r"\\b",
                definition,
            )
        )
        trigger_items.append({
            "table": table, "trigger": name,
            "function": func_schema + "." + func,
            "security_definer": bool(definer),
            "canonical_name_references_to_review": unqualified,
        })

    isolated_targets = sorted({
        row["referenced_table"] for row in fks
        if row["binds_isolated_canonical_copy"]
    })
    count = conn.execute(
        """SELECT count(*) FROM pg_catalog.pg_class t
           JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
           WHERE n.nspname=%s AND t.relkind IN ('r','p')
             AND t.relname LIKE 'warehouse_query_%%'""",
        (SCHEMA,),
    ).fetchone()[0]
    return {
        "database": database,
        "query_schema": SCHEMA,
        "canonical_schema": CANONICAL,
        "query_table_count": count,
        "foreign_keys": fks,
        "trigger_functions": trigger_items,
        "foreign_keys_to_isolated_canonical_copies": sum(
            bool(row["binds_isolated_canonical_copy"]) for row in fks
        ),
        "isolated_canonical_targets": isolated_targets,
        "functions_with_unqualified_canonical_references": sum(
            bool(row["canonical_name_references_to_review"])
            for row in trigger_items
        ),
        "safe_to_activate_query_api": False,
        "changes_applied": False,
    }


def main() -> int:
    try:
        with psycopg.connect(_dsn(), autocommit=False) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            report = inspect(conn)
            conn.rollback()
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print("CROSS_SCHEMA_DEPENDENCY_AUDIT_FAILED:",
              type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
