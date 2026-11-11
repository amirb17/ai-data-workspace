"""Read-only security inventory for the deployed isolated warehouse schema.

No role DDL, no GRANT/REVOKE, no SET ROLE, no mutation or cloud IO.
Reports the minimum facts needed to review separate PostgreSQL principals.
"""
from __future__ import annotations
import os
from pathlib import Path
import psycopg
from dotenv import dotenv_values

SCHEMA="datarise_warehouse_dev"
TABLES=(
    "users", "auth_sessions", "workspaces", "datasets",
    "warehouse_query_attempt_registry",
    "warehouse_query_api_requests",
    "warehouse_query_dispatch_checkpoints",
    "warehouse_query_dispatch_revocations",
    "warehouse_query_send_intents",
    "warehouse_query_send_journal",
    "warehouse_query_recovery_attestations",
    "warehouse_query_provider_policies",
    "warehouse_query_result_contracts",
    "warehouse_query_result_attestations",
    "query_governance_reservations",
    "query_governance_usage",
)

def dsn():
    if os.getenv("DATABASE_URL"): return os.environ["DATABASE_URL"]
    root=Path(__file__).resolve().parents[1]
    for path in (root/".env",Path("D:/Project/ai-data-workspace/.env")):
        if path.is_file():
            value=dotenv_values(path).get("DATABASE_URL")
            if value:return value
    raise RuntimeError("DATABASE_URL_UNAVAILABLE")

def main():
    with psycopg.connect(dsn(), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, current_schema()")
            db,login,default_schema=cur.fetchone()
            if db!="ai_data_workspace":
                raise RuntimeError("WRONG_DATABASE")
            cur.execute("""
                SELECT n.oid,pg_get_userbyid(n.nspowner),
                  has_schema_privilege('public',n.oid,'USAGE'),
                  has_schema_privilege('public',n.oid,'CREATE')
                FROM pg_namespace n WHERE n.nspname=%s
            """,(SCHEMA,))
            info=cur.fetchone()
            if info is None:raise RuntimeError("DEDICATED_SCHEMA_NOT_INSTALLED")
            oid,owner,public_usage,public_create=info
            print("TARGET:",db,SCHEMA)
            print("SCHEMA OWNER:",owner)
            print("PUBLIC SCHEMA USAGE/CREATE:",public_usage,public_create)
            cur.execute("""
                SELECT c.relname,pg_get_userbyid(c.relowner),
                       has_table_privilege('public',c.oid,'SELECT'),
                       has_table_privilege('public',c.oid,'INSERT'),
                       has_table_privilege('public',c.oid,'UPDATE'),
                       has_table_privilege('public',c.oid,'DELETE')
                FROM pg_class c WHERE c.relnamespace=%s
                  AND c.relkind IN ('r','p')
                  AND c.relname=ANY(%s)
                ORDER BY c.relname
            """,(oid,list(TABLES)))
            rows=cur.fetchall()
            for name,table_owner,select,insert,update,delete in rows:
                print("TABLE:",name,"owner=",table_owner,
                      "PUBLIC rights (S/I/U/D)=",select,insert,update,delete)
            missing=sorted(set(TABLES)-{row[0] for row in rows})
            if missing:print("MISSING:",", ".join(missing))
            cur.execute("""
                SELECT rolname,rolcanlogin,rolsuper,rolcreaterole,
                       rolcreatedb,rolreplication,rolbypassrls
                FROM pg_roles WHERE rolname LIKE 'datarise_warehouse_%'
                ORDER BY rolname
            """)
            roles=cur.fetchall()
            print("WAREHOUSE RESTRICTED ROLES:",len(roles))
            for role in roles:print("ROLE:",*role)
            cur.execute("""
                SELECT count(*), count(*) FILTER (WHERE prosecdef),
                       count(*) FILTER (WHERE proacl IS NULL)
                FROM pg_proc WHERE pronamespace=%s
                  AND proname LIKE 'warehouse_query_%'
            """,(oid,))
            print("QUERY FUNCTION COUNTS (total/definer/default ACL):",
                  cur.fetchone())
            cur.execute("""
                SELECT n.nspname,c.relname,pg_get_userbyid(c.relowner)
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public'
                  AND c.relname IN ('users','auth_sessions','workspaces')
                ORDER BY c.relname
            """)
            print("EXISTING APP IDENTITY TABLES:",cur.fetchall())
        conn.rollback()
    print("READ-ONLY AUDIT COMPLETE: no database changes")

if __name__=="__main__":main()
