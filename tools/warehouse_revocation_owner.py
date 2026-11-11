"""Least-privilege ownership for revocation triggers on canonical application tables.

Run inside the SAME PostgreSQL transaction that rebinds warehouse query FKs,
rewrites guards, and attaches canonical revocation hooks. No standalone GRANT.
A dedicated NOLOGIN role, not postgres or an API principal, executes the
security-definer revocation trigger. The function only inserts query revocation
records from trusted OLD row fields. Its search_path excludes public to avoid
shadowed query-protocol objects; public model source is explicitly qualified.

No runtime login receives these privileges. No user/session data is copied.
"""
from __future__ import annotations

from psycopg import sql

ISOLATED = "datarise_warehouse_dev"
OWNER = "datarise_wh_owner"
REVOCATION_FUNCTION = "warehouse_query_dispatch_revoke_notice"
READS = (
    "warehouse_query_dispatch_checkpoints",
    "warehouse_query_attempt_logical_drafts",
    "warehouse_query_attempt_publication_pins",
)


def install_revocation_owner(conn) -> None:
    role = conn.execute("""
        SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit,
               rolreplication, rolbypassrls,
               (SELECT count(*) FROM pg_auth_members m
                WHERE m.member=r.oid OR m.roleid=r.oid)
        FROM pg_roles r WHERE rolname=%s
    """, (OWNER,)).fetchone()
    if role != (False, False, False, False, False, False, False, 0):
        raise RuntimeError("REVOCATION_OWNER_ROLE_NOT_RESTRICTED")
    # A SECURITY DEFINER owner must not inherit rights from any other role.
    # Permissions granted here are the complete intended capability.
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
        sql.Identifier(ISOLATED), sql.Identifier(OWNER)
    ))
    for name in READS:
        conn.execute(sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
            sql.Identifier(ISOLATED), sql.Identifier(name), sql.Identifier(OWNER)
        ))
    conn.execute(sql.SQL("GRANT INSERT ON {}.{} TO {}").format(
        sql.Identifier(ISOLATED), sql.Identifier(
            "warehouse_query_dispatch_revocations"), sql.Identifier(OWNER)
    ))
    conn.execute(sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
        sql.Identifier("public"), sql.Identifier("warehouse_model_sources"),
        sql.Identifier(OWNER)
    ))
    # PostgreSQL requires the target owner to have CREATE privilege on the
    # containing schema when ownership is transferred. Provide it transiently
    # for the transactional ALTER and revoke it before commit.
    conn.execute(sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
        sql.Identifier(ISOLATED), sql.Identifier(OWNER)
    ))
    fn = sql.SQL("{}.{}()").format(
        sql.Identifier(ISOLATED), sql.Identifier(REVOCATION_FUNCTION)
    )
    conn.execute(sql.SQL("ALTER FUNCTION {} OWNER TO {}").format(
        fn, sql.Identifier(OWNER)
    ))
    conn.execute(sql.SQL("ALTER FUNCTION {} SECURITY DEFINER").format(fn))
    conn.execute(sql.SQL(
        "ALTER FUNCTION {} SET search_path TO pg_catalog, {}, pg_temp"
    ).format(fn, sql.Identifier(ISOLATED)))
    conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
        sql.Identifier(ISOLATED), sql.Identifier(OWNER)
    ))
    conn.execute(sql.SQL("REVOKE ALL ON FUNCTION {} FROM PUBLIC").format(fn))
    check = conn.execute("""
        SELECT p.prosecdef, pg_get_userbyid(p.proowner),
               has_schema_privilege(%s,%s,'CREATE'),
               p.proconfig
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=%s AND p.proname=%s
          AND p.prokind='f' AND p.pronargs=0
    """, (OWNER, ISOLATED, ISOLATED, REVOCATION_FUNCTION)).fetchone()
    if (check is None or check[0] is not True or check[1] != OWNER
            or check[2] is not False
            or not check[3] or
            not any(
                item == f"search_path=pg_catalog, {ISOLATED}, pg_temp"
                for item in check[3]
            )):
        raise RuntimeError("REVOCATION_OWNER_HARDENING_FAILED")
