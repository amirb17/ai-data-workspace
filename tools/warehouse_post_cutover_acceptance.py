"""Verify the committed warehouse cross-schema cutover without mutating data.

One-time post-apply acceptance for the local development database. Checks the
exact 13 canonical FK targets, the eight *public* revocation hooks, and the
restricted NOLOGIN SECURITY DEFINER owner. It reports database readiness
separately from API/runtime readiness; passing does NOT authorize cloud queries.

Usage: python -m tools.warehouse_post_cutover_acceptance
"""
from __future__ import annotations

import json
import sys

import psycopg

from tools.warehouse_deployment_boundary import _dsn
from tools.warehouse_query_fk_rebind_plan import TARGETS, ISOLATED, CANONICAL
from tools.warehouse_atomic_cutover import REVOKE_FUNCTION, REVOKE_TABLES
from tools.warehouse_revocation_owner import OWNER


def inspect(conn):
    if conn.execute("SELECT current_database()").fetchone()[0] != "ai_data_workspace":
        raise RuntimeError("WRONG_DATABASE")
    rows = conn.execute("""
        SELECT c.conname, sn.nspname, src.relname, rn.nspname, ref.relname
        FROM pg_constraint c
        JOIN pg_class src ON src.oid=c.conrelid
        JOIN pg_namespace sn ON sn.oid=src.relnamespace
        JOIN pg_class ref ON ref.oid=c.confrelid
        JOIN pg_namespace rn ON rn.oid=ref.relnamespace
        WHERE c.contype='f' AND sn.nspname=%s
          AND src.relname LIKE 'warehouse_query_%%'
          AND c.conname=ANY(%s)
    """, (ISOLATED, list(TARGETS))).fetchall()
    actual = {name:(source_schema,source,target_schema,target)
              for name,source_schema,source,target_schema,target in rows}
    expected = {name:(ISOLATED,source,CANONICAL,target)
                for name,(source,target) in TARGETS.items()}
    if actual != expected:
        raise RuntimeError("POST_CUTOVER_CANONICAL_FOREIGN_KEY_DRIFT")

    hooks = conn.execute("""
        SELECT n.nspname, t.relname, tr.tgname, pn.nspname,
               p.proname, p.prosecdef, pg_get_userbyid(p.proowner)
        FROM pg_trigger tr
        JOIN pg_class t ON t.oid=tr.tgrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        JOIN pg_proc p ON p.oid=tr.tgfoid
        JOIN pg_namespace pn ON pn.oid=p.pronamespace
        WHERE p.proname=%s AND pn.nspname=%s
          AND NOT tr.tgisinternal
    """, (REVOKE_FUNCTION, ISOLATED)).fetchall()
    if (len(hooks) != len(REVOKE_TABLES)
            or {row[1] for row in hooks} != REVOKE_TABLES
            or any(row[0] != CANONICAL or row[3] != ISOLATED
                   or row[4] != REVOKE_FUNCTION
                   or row[5] is not True or row[6] != OWNER for row in hooks)):
        raise RuntimeError("POST_CUTOVER_REVOCATION_TRIGGER_BOUNDARY_DRIFT")
    owner = conn.execute("""
        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
               rolreplication, rolbypassrls, rolinherit,
               (SELECT count(*) FROM pg_auth_members m
                WHERE m.roleid=r.oid OR m.member=r.oid)
        FROM pg_roles r WHERE r.rolname=%s
    """, (OWNER,)).fetchone()
    if owner != (False,False,False,False,False,False,False,0):
        raise RuntimeError("POST_CUTOVER_OWNER_ROLE_DRIFT")
    unexpected = conn.execute("""
        SELECT p.proname, pg_get_userbyid(p.proowner)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=%s AND p.proname LIKE 'warehouse_query_%%'
          AND p.prosecdef AND NOT (
              p.proname=%s AND pg_get_userbyid(p.proowner)=%s
          )
        LIMIT 1
    """, (ISOLATED, REVOKE_FUNCTION, OWNER)).fetchone()
    if unexpected:
        raise RuntimeError("POST_CUTOVER_UNEXPECTED_SECURITY_DEFINER")
    return {
        "canonical_foreign_keys_verified": len(actual),
        "canonical_revocation_triggers_verified": len(hooks),
        "restricted_revocation_owner_verified": True,
        "mutation_performed": False,
        "query_runtime_enabled": False,
        "next_gate": "WORKER_DB_GRANTS_AND_AUTHENTICATED_RUNTIME",
    }


def main():
    try:
        with psycopg.connect(_dsn(), autocommit=False) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            output=inspect(conn)
            conn.rollback()
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print("POST_CUTOVER_ACCEPTANCE_BLOCKED:", type(exc).__name__,
              str(exc) if isinstance(exc, RuntimeError) else "",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
