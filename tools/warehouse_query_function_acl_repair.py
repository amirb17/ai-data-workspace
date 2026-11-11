"""Inspect and optionally remove direct PUBLIC EXECUTE on warehouse query routines.

Only targets the isolated development schema. Default is read-only. --apply
executes reviewed REVOKE commands atomically, without editing table ACLs,
granting runtime privileges, or enabling BigQuery. Existing users/workspaces
in the public schema remain untouched.

Example:
  python tools/warehouse_query_function_acl_repair.py
  python tools/warehouse_query_function_acl_repair.py --apply
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import dotenv_values

SCHEMA = "datarise_warehouse_dev"


def _dsn():
    value = os.getenv("DATABASE_URL")
    if value:
        return value
    root = Path(__file__).resolve().parents[1]
    for path in (root / ".env", Path("D:/Project/ai-data-workspace/.env")):
        if path.is_file():
            value = dotenv_values(path).get("DATABASE_URL")
            if value:
                return value
    raise RuntimeError("DATABASE_URL_UNAVAILABLE")


def audit(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, current_schema()")
        database, user, default_schema = cur.fetchone()
        if database != "ai_data_workspace" or default_schema != "public":
            raise RuntimeError("UNAPPROVED_DATABASE_CONTEXT")
        if user != "postgres":
            raise RuntimeError("REVIEWED_LOCAL_SCHEMA_OWNER_REQUIRED")
        cur.execute("""
            SELECT p.oid, p.proname, pg_get_function_identity_arguments(p.oid),
                   p.prosecdef, pg_get_userbyid(p.proowner),
                   p.proacl IS NULL,
                   EXISTS (
                       SELECT 1
                       FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                       WHERE a.grantee=0 AND a.privilege_type='EXECUTE'
                   )
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=%s AND p.proname LIKE %s
            ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)
        """, (SCHEMA, "warehouse_query_%"))
        rows = cur.fetchall()
        if len(rows) != 22:
            raise RuntimeError("QUERY_FUNCTION_COUNT_DRIFT")
        if any(definer or owner != "postgres" for _, _, _, definer, owner, _, _ in rows):
            raise RuntimeError("FUNCTION_OWNER_OR_SECURITY_DRIFT")
        print("QUERY FUNCTIONS:", len(rows))
        candidates = []
        for oid, name, args, _definer, _owner, default_acl, public_exec in rows:
            if default_acl or public_exec:
                print("PUBLIC EXECUTE:", name + "(" + args + ")",
                      "default_acl=" + str(default_acl))
            if public_exec:
                candidates.append((name, args))
        return rows, candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Atomically revoke PUBLIC EXECUTE on these query routines")
    args = parser.parse_args()
    with psycopg.connect(_dsn(), autocommit=False) as conn:
        try:
            rows, candidates = audit(conn)
            if args.apply:
                for name, signature in candidates:
                    with conn.cursor() as cur:
                        cur.execute(sql.SQL("REVOKE ALL ON FUNCTION {}.{}({}) FROM PUBLIC").format(
                            sql.Identifier(SCHEMA), sql.Identifier(name), sql.SQL(signature)
                        ))
                _rows, remaining = audit(conn)
                if remaining:
                    raise RuntimeError("PUBLIC_FUNCTION_EXECUTE_REMAINING")
                conn.commit()
                print("APPLIED: PUBLIC query function EXECUTE removed;", len(candidates),
                      "function(s) hardened")
            else:
                conn.rollback()
                print("READ-ONLY REVIEW: no changes;", len(candidates),
                      "function(s) requiring PUBLIC EXECUTE revocation")
        except BaseException:
            conn.rollback()
            raise


if __name__ == "__main__":
    main()
