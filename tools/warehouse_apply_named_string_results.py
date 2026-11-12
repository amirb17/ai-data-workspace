"""Install the reviewed 0063 bounded STRING result migration in local development.

Default: perform PostgreSQL DDL checks, then ROLLBACK.
Explicit --apply is required to commit. Does not touch AWS or BigQuery.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import psycopg

from tools.warehouse_deployment_boundary import _dsn

SCHEMA = "datarise_warehouse_dev"
FILE = Path(__file__).resolve().parents[1] / "migrations" / "0063_warehouse_named_string_results.sql"


def execute(*, apply: bool = False) -> dict:
    script = FILE.read_text(encoding="utf-8")
    if (not script.startswith("-- 0063:")
            or "\nBEGIN;\n" not in script
            or not script.rstrip().endswith("COMMIT;")):
        raise RuntimeError("REVIEWED_MIGRATION_FORMAT_REQUIRED")
    # The containing Python transaction owns COMMIT/ROLLBACK, not a SQL file.
    script = script.replace("\nBEGIN;\n", "\n", 1)
    script = script[:script.rfind("COMMIT;")]
    with psycopg.connect(_dsn(), autocommit=False) as conn:
        try:
            identity = conn.execute(
                "SELECT current_database(),session_user"
            ).fetchone()
            if identity != ("ai_data_workspace", "postgres"):
                raise RuntimeError("LOCAL_POSTGRES_ADMIN_REQUIRED")
            conn.execute(
                "SET LOCAL search_path TO datarise_warehouse_dev,public,pg_catalog,pg_temp"
            )
            schema = conn.execute("SELECT current_schema()").fetchone()[0]
            if schema != SCHEMA:
                raise RuntimeError("ISOLATED_WAREHOUSE_SCHEMA_REQUIRED")
            if conn.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname LIKE 'warehouse_query_%'
                    AND c.relkind IN ('r','p','v','m','f','S')
                )
            """).fetchone()[0]:
                raise RuntimeError("PUBLIC_QUERY_PROTOCOL_SHADOWING")
            conn.execute(script)
            version = conn.execute("""
                SELECT pg_get_functiondef(p.oid)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=%s AND p.proname='warehouse_query_named_result_guard'
            """, (SCHEMA,)).fetchone()
            if not version or "'STRING'" not in version[0]:
                raise RuntimeError("BOUNDED_STRING_GUARD_NOT_APPLIED")
            if apply:
                conn.commit()
            else:
                conn.rollback()
            return dict(
                migration="0063", schema=SCHEMA,
                status="APPLIED" if apply else "ROLLBACK_ONLY",
                cloud_queries_submitted=0,
            )
        except BaseException:
            conn.rollback()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(execute(apply=args.apply), sort_keys=True, indent=2))
        return 0
    except Exception as error:
        print("WAREHOUSE_STRING_MIGRATION_BLOCKED:",
              type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
