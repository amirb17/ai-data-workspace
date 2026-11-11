"""Provision and verify the *identity-only* warehouse PostgreSQL roles.

Default: read-only, prints missing/unsafe role count. --apply uses the reviewed
deployment/warehouse_dev_runtime_identities.sql, creating only nonprivileged
LOGIN identities and a NOLOGIN owner. Does not assign passwords, membership,
schema privileges, table rights, runtime factories or cloud credentials.
No query API enablement or data migration; public workspace remains canonical.

Run from the repository root:
  python -m tools.warehouse_provision_roles
  python -m tools.warehouse_provision_roles --apply

When --apply is used, this requires the local PostgreSQL superuser used by the
existing development database (postgres); it fails before DDL otherwise.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import psycopg
from dotenv import dotenv_values

from app.db.warehouse_restricted_connection import ROLE_BY_DUTY

ROOT = Path(__file__).resolve().parents[1]
DDL = ROOT / "deployment" / "warehouse_dev_runtime_identities.sql"
SCHEMA = "datarise_warehouse_dev"
EXPECTED = {"datarise_wh_owner": False, **{v: True for v in ROLE_BY_DUTY.values()}}


def _dsn() -> str:
    value = os.environ.get("DATABASE_URL")
    if value:
        return value
    for path in (ROOT / ".env", Path("D:/Project/ai-data-workspace/.env")):
        if path.is_file():
            value = dotenv_values(path).get("DATABASE_URL")
            if value:
                return value
    raise RuntimeError("DATABASE_URL_UNAVAILABLE")


def audit(conn) -> list[str]:
    db, role, superuser = conn.execute(
        """SELECT current_database(), session_user,
                  (SELECT rolsuper FROM pg_roles WHERE rolname=session_user)"""
    ).fetchone()
    if db != "ai_data_workspace" or role != "postgres" or not superuser:
        raise RuntimeError("REVIEWED_LOCAL_ADMIN_CONTEXT_REQUIRED")
    exists = conn.execute(
        "SELECT 1 FROM pg_namespace WHERE nspname=%s", (SCHEMA,)
    ).fetchone()
    if not exists:
        raise RuntimeError("WAREHOUSE_DEV_SCHEMA_MISSING")
    errors = []
    for name, can_login in EXPECTED.items():
        row = conn.execute(
            """SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                      rolreplication, rolbypassrls, rolinherit,
                      (SELECT count(*) FROM pg_auth_members m
                       WHERE m.member=r.oid OR m.roleid=r.oid)
               FROM pg_roles r WHERE rolname=%s""", (name,)
        ).fetchone()
        if row is None:
            errors.append("MISSING:" + name)
        elif (row[0] is not can_login or any(row[1:7]) or row[7] != 0):
            errors.append("UNSAFE:" + name)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        with psycopg.connect(_dsn(), autocommit=False) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            pending = audit(conn)
            conn.rollback()
            if any(item.startswith("UNSAFE:") for item in pending):
                raise RuntimeError("EXISTING_WAREHOUSE_ROLE_UNSAFE")
            print("ROLE INVENTORY: missing", len(pending),
                  "of", len(EXPECTED), flush=True)
            if not args.apply:
                print("READ ONLY: use --apply to create missing role identities")
                return 0 if not pending else 3
            if pending:
                ddl = DDL.read_text(encoding="utf-8-sig")
                # Execute exactly the repository-owned reviewable SQL, never
                # build GRANT or CREATE USER commands from caller input.
                if ("CREATE ROLE %I" not in ddl
                        or "WAREHOUSE_LOCAL_DEV_ADMIN_CONTEXT_REQUIRED" not in ddl):
                    raise RuntimeError("IDENTITY_PROVISION_SQL_UNEXPECTED")
                conn.execute(ddl)
                conn.commit()
            conn.execute("SET TRANSACTION READ ONLY")
            after = audit(conn)
            conn.rollback()
            if after:
                raise RuntimeError("IDENTITY_PROVISION_INCOMPLETE")
            print("ROLE IDENTITIES VERIFIED:", len(EXPECTED),
                  "(no data privileges, no passwords, runtime disabled)")
            return 0
    except Exception as exc:
        print("ROLE PROVISION BLOCKED:", type(exc).__name__,
              str(exc) if isinstance(exc, RuntimeError) else "",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
