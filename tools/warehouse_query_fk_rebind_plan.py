"""Generate a reviewed, read-only plan to rebind warehouse query FKs.

The live inventory found 13 foreign keys from isolated query tables to empty
isolated copies of canonical application objects. These must eventually point
to public, *together with* trigger rewrites and service authorization changes.

This tool intentionally DOES NOT execute DDL. In particular, it cannot install
only the new FKs while the existing SECURITY INVOKER trigger functions still
read empty isolated tables. It fails closed on catalog drift, data present in
query protocol, missing canonical target, or inconsistent column identities.

  python -m tools.warehouse_query_fk_rebind_plan
"""
from __future__ import annotations

import json
import sys

import psycopg
from psycopg import sql

from tools.warehouse_deployment_boundary import _dsn

ISOLATED = "datarise_warehouse_dev"
CANONICAL = "public"
# Reviewed directly against the local PostgreSQL catalog inventory.
TARGETS = {
    "warehouse_query_api_requests_user_id_fkey": ("warehouse_query_api_requests", "users"),
    "warehouse_query_api_requests_workspace_id_fkey": ("warehouse_query_api_requests", "workspaces"),
    "warehouse_query_attempt_logic_workspace_id_warehouse_model_fkey": ("warehouse_query_attempt_logical_drafts", "warehouse_model_versions"),
    "warehouse_query_attempt_publica_resource_id_publication_id_fkey": ("warehouse_query_attempt_publication_pins", "warehouse_publications"),
    "warehouse_query_attempt_registry_owner_key_fkey": ("warehouse_query_attempt_registry", "users"),
    "warehouse_query_attempt_registry_workspace_id_fkey": ("warehouse_query_attempt_registry", "workspaces"),
    "warehouse_query_attempt_reservation_links_reservation_id_fkey": ("warehouse_query_attempt_reservation_links", "query_governance_reservations"),
    "warehouse_query_dispatch_checkpoints_reservation_id_fkey": ("warehouse_query_dispatch_checkpoints", "query_governance_reservations"),
    "warehouse_query_dispatch_checkpoints_user_id_fkey": ("warehouse_query_dispatch_checkpoints", "users"),
    "warehouse_query_dispatch_checkpoints_workspace_id_fkey": ("warehouse_query_dispatch_checkpoints", "workspaces"),
    "warehouse_query_publication_ret_resource_id_publication_id_fkey": ("warehouse_query_publication_retirement_fences", "warehouse_publications"),
    "warehouse_query_result_contracts_workspace_id_fkey": ("warehouse_query_result_contracts", "workspaces"),
    "warehouse_query_send_intents_reservation_id_fkey": ("warehouse_query_send_intents", "query_governance_reservations"),
}


def plan(conn):
    if conn.execute("SELECT current_database()").fetchone()[0] != "ai_data_workspace":
        raise RuntimeError("WRONG_DATABASE")
    rows = conn.execute("""
        SELECT c.conname, src.relname, ref.relname, rn.nspname,
               c.conkey, c.confkey, c.confupdtype, c.confdeltype,
               c.confmatchtype, c.condeferrable, c.condeferred,
               c.convalidated,
               (SELECT array_agg(a.attname ORDER BY k.ordinality)
                FROM unnest(c.conkey) WITH ORDINALITY k(attnum, ordinality)
                JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum),
               (SELECT array_agg(a.attname ORDER BY k.ordinality)
                FROM unnest(c.confkey) WITH ORDINALITY k(attnum, ordinality)
                JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.attnum)
          FROM pg_constraint c
          JOIN pg_class src ON src.oid=c.conrelid
          JOIN pg_namespace sn ON sn.oid=src.relnamespace
          JOIN pg_class ref ON ref.oid=c.confrelid
          JOIN pg_namespace rn ON rn.oid=ref.relnamespace
         WHERE sn.nspname=%s AND src.relname LIKE 'warehouse_query_%%'
           AND c.contype='f'
           AND rn.nspname=%s
         ORDER BY c.conname
    """, (ISOLATED, ISOLATED)).fetchall()
    actual = {row[0]: (row[1], row[2]) for row in rows
              if row[2] in {target for _, target in TARGETS.values()}}
    if actual != TARGETS:
        raise RuntimeError("FOREIGN_KEY_TARGET_CATALOG_DRIFT")

    # We cannot safely split migration across trigger/function/role phases once
    # any query protocol row already exists. Prevent rolling back authority by
    # migrating a populated attempt ledger.
    tables = sorted({source for source, _ in TARGETS.values()})
    nonempty = []
    for name in tables:
        found = conn.execute(
            sql.SQL("SELECT EXISTS(SELECT 1 FROM {}.{} LIMIT 1)").format(
                sql.Identifier(ISOLATED), sql.Identifier(name)
            )
        ).fetchone()[0]
        if found:
            nonempty.append(name)
    if nonempty:
        raise RuntimeError("POPULATED_QUERY_PROTOCOL_REQUIRES_DATA_MIGRATION")

    updates = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE",
               "n": "SET NULL", "d": "SET DEFAULT"}
    matches = {"s": "SIMPLE", "f": "FULL", "p": "PARTIAL"}
    commands = []
    for row in rows:
        (name, source, target, target_schema, _src_keys, _dst_keys,
         update, delete, match, defer, deferred, valid,
         src_columns, dst_columns) = row
        if name not in TARGETS:
            continue
        if target_schema != ISOLATED or not src_columns or not dst_columns or len(src_columns) != len(dst_columns):
            raise RuntimeError("INVALID_FK_COLUMN_MAPPING")
        # Require the exact referenced column identities to exist in public.
        public_columns = conn.execute("""
            SELECT a.attname FROM pg_attribute a
            JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=%s
              AND a.attname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped
        """, (CANONICAL, target, dst_columns)).fetchall()
        if {r[0] for r in public_columns} != set(dst_columns):
            raise RuntimeError("CANONICAL_REFERENCED_COLUMNS_MISSING")
        if not valid or match not in matches or update not in updates or delete not in updates:
            raise RuntimeError("NONSTANDARD_FK_REQUIRES_REVIEW")
        source_id = sql.SQL("{}.{}").format(sql.Identifier(ISOLATED), sql.Identifier(source))
        dest_id = sql.SQL("{}.{}").format(sql.Identifier(CANONICAL), sql.Identifier(target))
        # A future atomic migration must add the replacement first under a
        # temporary constraint name, validate it, and drop the old FK in one
        # transaction with trigger/repository/ACL changes. This output is
        # advisory, not an executable migration.
        commands.append({
            "constraint": name, "source": f"{ISOLATED}.{source}",
            "canonical_target": f"{CANONICAL}.{target}",
            "columns": src_columns, "referenced_columns": dst_columns,
            "on_update": updates[update], "on_delete": updates[delete],
            "match": matches[match], "deferrable": defer,
            "initially_deferred": deferred,
        })
    return {
        "database": "ai_data_workspace",
        "query_schema": ISOLATED,
        "canonical_schema": CANONICAL,
        "rebind_fk_count": len(commands),
        "constraints": commands,
        "query_protocol_empty": True,
        "ddl_executed": False,
        "blocked_until": [
            "guard_and_revocation_triggers_rebound_to_public_canonical_tables",
            "query_service_references_qualified_and_role_ownership_reviewed",
            "atomic_fk_rebind_and_trigger_rewrite_reviewed",
            "restricted_runtime_db_grants_and_independent_worker_acceptance",
        ],
    }


def main():
    try:
        with psycopg.connect(_dsn(), autocommit=False) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            result = plan(conn)
            conn.rollback()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print("QUERY_FK_REBIND_PLAN_BLOCKED:", type(exc).__name__,
              str(exc) if isinstance(exc, RuntimeError) else "",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
