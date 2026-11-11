"""Build an isolated development warehouse schema from the canonical migrations.

No existing public tables are altered. Default: execute the full migration chain
transactionally and ROLLBACK. --apply persists only a brand-new schema, after
the same preflight. This creates an EMPTY development database namespace, not a
copy of authentication/workspace data and not production runtime authorization.

Run from the repository root with an approved local PostgreSQL admin DSN:
  python tools/warehouse_isolated_schema_bootstrap.py
  python tools/warehouse_isolated_schema_bootstrap.py --apply

No API enablement, IAM grants, cloud IO, or runtime role creation.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "datarise_warehouse_dev"
DB_NAME = "ai_data_workspace"
# Each numbered migration, including BOTH independent 0048 files, in
# deterministic filename order. No rollback/pending/experimental migration.
FILES = tuple(sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")))
START = re.compile(r"(?m)^BEGIN;[ \t]*(?:\r?\n|$)")
END = re.compile(r"(?m)^COMMIT;[ \t]*(?:\r?\n|$)")
MIGRATION_NAME = re.compile(r"[0-9]{4}_[a-z0-9_]+\.sql")


def _dsn():
    value = os.getenv("DATABASE_URL")
    if value:
        return value
    for path in (ROOT / ".env", Path("D:/Project/ai-data-workspace/.env")):
        if path.is_file():
            value = dotenv_values(path).get("DATABASE_URL")
            if value:
                return value
    raise RuntimeError("DATABASE_URL_UNAVAILABLE")


def _sql_file(path):
    # Filenames are fixed by the repository, never supplied by the caller.
    if not MIGRATION_NAME.fullmatch(path.name):
        raise RuntimeError("INVALID_MIGRATION_FILENAME")
    raw = path.read_text(encoding="utf-8-sig")
    starts = list(START.finditer(raw))
    ends = list(END.finditer(raw))
    # Canonical migration files either have one top-level BEGIN/COMMIT pair
    # or no wrapper. Reject unexpected/multiple transaction delimiters.
    if len(starts) != len(ends) or len(starts) > 1:
        raise RuntimeError(f"AMBIGUOUS_MIGRATION_TRANSACTION: {path.name}")
    if starts:
        start, end = starts[0], ends[0]
        if start.start() >= end.start() or raw[end.end():].strip():
            raise RuntimeError(f"UNSAFE_MIGRATION_TRANSACTION: {path.name}")
        prefix = raw[:start.start()]
        # Only SQL comments and whitespace can precede the delimiter.
        if re.sub(r"(?m)^\s*--[^\n]*(?:\n|$)", "", prefix).strip():
            raise RuntimeError(f"UNSAFE_MIGRATION_PREFIX: {path.name}")
        raw = prefix + raw[start.end():end.start()]
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Persist a fresh isolated development schema")
    args = parser.parse_args()
    if not FILES or FILES[0].name != "0001_baseline.sql" or not any(
            p.name == "0062_warehouse_named_query_results.sql" for p in FILES):
        raise RuntimeError("INCOMPLETE_MIGRATION_CHAIN")
    scripts = [(path.name, _sql_file(path)) for path in FILES]
    print(f"CHAIN: {len(scripts)} migration files; schema={SCHEMA}")

    with psycopg.connect(_dsn(), autocommit=False) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_schema(), current_user")
                db, schema, _login = cur.fetchone()
                if db != DB_NAME or schema != "public":
                    raise RuntimeError("UNAPPROVED_DATABASE_TARGET")
                cur.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname=%s", (SCHEMA,)
                )
                if cur.fetchone():
                    raise RuntimeError("DEDICATED_SCHEMA_ALREADY_EXISTS")
                # Prevent concurrent bootstrap attempts.
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                            ("datarise-warehouse-dev-schema-bootstrap",))
                cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)))
                # Scope ALL unqualified DDL, grants, triggers and functions to
                # this schema; never resolve names through public.
                cur.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog, pg_temp")
                            .format(sql.Identifier(SCHEMA)))
                cur.execute("SELECT current_schema()")
                if cur.fetchone()[0] != SCHEMA:
                    raise RuntimeError("SCHEMA_PINNING_FAILED")
            for name, script in scripts:
                with conn.cursor() as cur:
                    cur.execute(script)
                print(f"OK: {name}")
            with conn.cursor() as cur:
                cur.execute(sql.SQL("""
                    SELECT count(*) FROM information_schema.tables
                    WHERE table_schema = %s AND table_name=ANY(%s)
                """), (SCHEMA, [
                    "warehouse_query_attempt_registry",
                    "warehouse_query_api_requests",
                    "warehouse_query_result_contracts",
                    "warehouse_query_result_attestations",
                    "auth_sessions", "workspaces",
                ]))
                if cur.fetchone()[0] != 6:
                    raise RuntimeError("EXPECTED_BASE_AND_QUERY_TABLES_MISSING")
            if args.apply:
                conn.commit()
                print("APPLIED: new isolated development schema committed")
            else:
                conn.rollback()
                print("DRY RUN PASS: all migrations worked; schema rolled back")
        except BaseException:
            conn.rollback()
            raise


if __name__ == "__main__":
    main()
