"""Atomic, fail-closed installation of existing warehouse query SQL migrations.

Safety inventory only: this historical public-schema runner is disabled because
migration 0057 deliberately rejects public and all system schemas.
Never installs PostgreSQL roles, grants application authority, or enables HTTP
query execution. Use only against an explicitly approved local development DB.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    "0048_warehouse_query_attempt_registry.sql",
    "0049_warehouse_query_attempt_publication_pins.sql",
    "0050_warehouse_query_reservation_publication_links.sql",
    "0051_warehouse_query_publication_retirement_fences.sql",
    "0052_warehouse_query_attempt_logical_drafts.sql",
    "0053_warehouse_query_dispatch_checkpoints.sql",
    "0054_warehouse_query_send_recovery.sql",
    "0055_warehouse_query_recovery_attestations.sql",
    "0056_warehouse_query_provider_policies.sql",
    "0057_warehouse_query_function_acl_hardening.sql",
    "0058_warehouse_query_request_envelopes.sql",
    "0059_warehouse_query_cancel_intents.sql",
    "0060_warehouse_query_cancel_attempt_boundary.sql",
    "0061_warehouse_query_api_requests.sql",
    "0062_warehouse_named_query_results.sql",
)
BASE_TABLES = (
    "workspaces",
    "users",
    "auth_sessions",
    "query_governance_reservations",
    "query_governance_usage",
    "warehouse_models",
    "warehouse_model_versions",
    "warehouse_model_sources",
    "warehouse_resources",
    "warehouse_publications",
    "warehouse_publication_heads",
)
EXPECTED_NEW_TABLES = (
    "warehouse_query_attempt_registry",
    "warehouse_query_api_requests",
    "warehouse_query_result_contracts",
    "warehouse_query_result_attestations",
)
# Strip only a migration's standalone transaction delimiters; keep its DDL
# and PL/pgSQL function bodies unchanged.
START = re.compile(r"(?m)^BEGIN;[ \t]*(?:\r?\n|$)")
END = re.compile(r"(?m)^COMMIT;[ \t]*(?:\r?\n|$)")


def _database_url():
    # Never print secrets, connect strings or environment contents.
    value = os.getenv("DATABASE_URL")
    if value:
        return value
    for path in (ROOT / ".env", Path("D:/Project/ai-data-workspace/.env")):
        if path.exists():
            value = dotenv_values(path).get("DATABASE_URL")
            if value:
                return value
    raise RuntimeError("DATABASE_URL_UNAVAILABLE")


def _files():
    result = []
    for name in MIGRATIONS:
        path = ROOT / "migrations" / name
        raw = path.read_text(encoding="utf-8-sig")
        start = START.search(raw)
        endings = list(END.finditer(raw))
        if start is None:
            raise RuntimeError(f"MIGRATION_WRAPPER_MISSING: {name}")
        if len(endings) != 1 or endings[0].start() <= start.end():
            raise RuntimeError(f"MIGRATION_COMMIT_MISSING_OR_AMBIGUOUS: {name}")
        # Only remove the top-level delimiters, never transaction control
        # appearing within a PL/pgSQL function definition.
        if raw[endings[0].end():].strip():
            raise RuntimeError(f"MIGRATION_TRAILING_SQL: {name}")
        result.append((name, raw[:start.start()] + raw[start.end():endings[0].start()]))
    return result


def _preflight(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_schema(), current_user")
        db, schema, role = cur.fetchone()
        print(f"TARGET: database={db} schema={schema} login={role}")
        if db != "ai_data_workspace" or schema != "public":
            raise RuntimeError("UNAPPROVED_DATABASE_OR_SCHEMA")
        # Do not alter existing query protocol or overwrite partial deployments.
        cur.execute("""
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public'
            AND c.relkind IN ('r','p','v','m','S')
            AND c.relname LIKE 'warehouse_query_%'
            ORDER BY c.relname
        """)
        existing = [row[0] for row in cur.fetchall()]
        if existing:
            raise RuntimeError("QUERY_OBJECTS_ALREADY_PRESENT: " + ", ".join(existing))
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_name=ANY(%s)
        """, (list(BASE_TABLES),))
        present = {row[0] for row in cur.fetchall()}
        missing = sorted(set(BASE_TABLES) - present)
        if missing:
            raise RuntimeError("BASE_TABLES_MISSING: " + ", ".join(missing))
        cur.execute("SELECT to_regprocedure('public.query_governance_no_truncate()') IS NOT NULL")
        if not cur.fetchone()[0]:
            raise RuntimeError("QUERY_GOVERNANCE_TRIGGER_DEPENDENCY_MISSING")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Unavailable: public schema is explicitly prohibited")
    args = parser.parse_args()
    # Do not prepare a partial migration only to fail halfway at 0057.
    # The source migration intentionally refuses public; do not bypass it.
    raise RuntimeError(
        "PUBLIC_SCHEMA_MIGRATION_PROHIBITED: 0057 explicitly rejects public. "
        "No DDL was run. Use a reviewed dedicated warehouse schema and "
        "separate restricted PostgreSQL identities; never disable 0057."
    )
    with psycopg.connect(_database_url(), autocommit=False) as conn:
        try:
            _preflight(conn)
            for name, sql in scripts:
                with conn.cursor() as cur:
                    cur.execute(sql)
                print(f"OK: {name}")
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT count(*) FROM information_schema.tables
                    WHERE table_schema='public' AND table_name=ANY(%s)
                """, (list(EXPECTED_NEW_TABLES),))
                if cur.fetchone()[0] != len(EXPECTED_NEW_TABLES):
                    raise RuntimeError("EXPECTED_QUERY_TABLES_NOT_INSTALLED")
            if args.apply:
                conn.commit()
                print("APPLIED: query migration chain committed atomically")
            else:
                conn.rollback()
                print("DRY RUN PASS: all 15 scripts executed; changes rolled back")
        except BaseException:
            conn.rollback()
            raise


if __name__ == "__main__":
    main()
