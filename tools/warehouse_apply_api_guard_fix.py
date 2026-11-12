"""Rollback-first repair of admission trigger schema resolution (migration 0064).

The 0061 trigger validates live authentication in public.users/auth_sessions/
workspaces, but its stored search_path omitted public. This script changes
ONLY the function configuration; no trigger body, permissions, user/session
data, durable requests or cloud jobs are modified.

  python -m tools.warehouse_apply_api_guard_fix
  python -m tools.warehouse_apply_api_guard_fix --apply
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import psycopg

from tools.warehouse_deployment_boundary import _dsn

SCHEMA = "datarise_warehouse_dev"
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / (
    "0064_warehouse_api_guard_canonical_search_path.sql"
)
EXPECTED_CONFIG = "search_path=pg_catalog, datarise_warehouse_dev, public, pg_temp"


def execute(*, apply: bool = False):
    original = MIGRATION.read_text(encoding="utf-8")
    if (not original.startswith("-- 0064:")
            or "\nBEGIN;\n" not in original
            or not original.rstrip().endswith("COMMIT;")):
        raise RuntimeError("REVIEWED_0064_MIGRATION_REQUIRED")
    sql = original.replace("\nBEGIN;\n", "\n", 1)
    sql = sql[:sql.rfind("COMMIT;")]
    with psycopg.connect(_dsn(), autocommit=False) as conn:
        try:
            name, login = conn.execute(
                "SELECT current_database(), session_user"
            ).fetchone()
            if (name, login) != ("ai_data_workspace", "postgres"):
                raise RuntimeError("LOCAL_DATABASE_ADMIN_REQUIRED")
            conn.execute(
                "SET LOCAL search_path TO datarise_warehouse_dev,public,pg_catalog,pg_temp"
            )
            if conn.execute("SELECT current_schema()").fetchone()[0] != SCHEMA:
                raise RuntimeError("ISOLATED_WAREHOUSE_SCHEMA_REQUIRED")
            conn.execute(sql)
            settings = conn.execute("""
                SELECT p.proconfig, p.prosecdef, p.prokind, p.provolatile,
                       pg_get_functiondef(p.oid)
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=%s AND p.proname='warehouse_query_api_request_guard'
            """, (SCHEMA,)).fetchone()
            if not settings:
                raise RuntimeError("AUTHENTICATED_API_GUARD_MISSING")
            config, security_definer, kind, volatility, definition = settings
            if (EXPECTED_CONFIG not in (config or ())
                    or security_definer
                    or kind != "f"
                    or "auth_sessions" not in definition
                    or "Current authenticated API request binding required" not in definition):
                raise RuntimeError("AUTH_GUARD_CONTRACT_CHANGED")
            guard = conn.execute("""
                SELECT 1 FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=%s AND c.relname='warehouse_query_api_requests'
                  AND t.tgname='warehouse_api_request_guard' AND NOT t.tgisinternal
                  AND t.tgenabled='O'
            """, (SCHEMA,)).fetchone()
            if not guard:
                raise RuntimeError("AUTH_GUARD_TRIGGER_DISABLED")
            if apply:
                conn.commit()
            else:
                conn.rollback()
            return {
                "migration": "0064",
                "status": "APPLIED" if apply else "ROLLBACK_ONLY",
                "trigger": "warehouse_query_api_request_guard",
                "authentication_checks_preserved": True,
                "provider_job_submitted": False,
            }
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
        print("WAREHOUSE_API_GUARD_REPAIR_BLOCKED:", type(error).__name__,
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
