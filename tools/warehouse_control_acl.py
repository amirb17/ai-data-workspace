"""Transactionally install and verify narrow query-control PostgreSQL ACLs.

This is a development DB credential boundary, NOT a provider activation switch.
Application users/sessions and query governance remain canonical in public;
query protocol tables remain in datarise_warehouse_dev.

Default: execute GRANTs and checks then ROLLBACK. --apply commits only after
all assertions pass. Review the manifest before applying; never run on RDS.
No role passwords are installed. No provider send/attest/account/read rights
are granted, and API execution remains disabled.
"""
from __future__ import annotations

import argparse
import json
import sys
import psycopg
from psycopg import sql

from tools.warehouse_deployment_boundary import _dsn
from tools.warehouse_post_cutover_acceptance import inspect as verify_cutover

CONTROL = "datarise_wh_control"
SCHEMA = "datarise_warehouse_dev"
# Admission needs bounded access to current published models and identities;
# it cannot change canonical auth, datasets, or warehouse publications.
PUBLIC_SELECT = (
    "users", "auth_sessions", "workspaces", "datasets",
    "warehouse_models", "warehouse_model_versions",
    "warehouse_model_configurations", "warehouse_model_sources",
    "warehouse_resources", "warehouse_publications",
    "warehouse_publication_heads", "warehouse_serving_materializations",
    "query_governance_reservations", "query_governance_usage",
)
# Query governance reserve() writes a reservation and takes a workspace lock.
PUBLIC_WRITES = {
    "query_governance_reservations": ("INSERT", "UPDATE"),
}
# These query protocol objects are written only during authenticated
# admission/checkpoint preparation in the query-control process.
QUERY_WRITES = {
    "warehouse_query_attempt_registry": ("INSERT",),
    "warehouse_query_attempt_logical_drafts": ("INSERT",),
    "warehouse_query_attempt_publication_pins": ("INSERT",),
    "warehouse_query_attempt_reservation_links": ("INSERT",),
    "warehouse_query_api_requests": ("INSERT",),
    "warehouse_query_result_contracts": ("INSERT",),
    "warehouse_query_dispatch_checkpoints": ("INSERT",),
    "warehouse_query_send_intents": ("INSERT",),
    "warehouse_query_provider_policies": ("INSERT",),
    "warehouse_query_request_envelopes": ("INSERT",),
    "warehouse_query_send_journal": ("INSERT",),
}
# All 17 bounded query protocol tables, reviewed against live dependency report.
# Read-only access to the other protocol stages is used for status/recovery.
QUERY_SELECT = (
    "warehouse_query_attempt_registry",
    "warehouse_query_attempt_logical_drafts",
    "warehouse_query_attempt_publication_pins",
    "warehouse_query_attempt_reservation_links",
    "warehouse_query_dispatch_checkpoints",
    "warehouse_query_dispatch_revocations",
    "warehouse_query_api_requests",
    "warehouse_query_result_contracts",
    "warehouse_query_send_intents",
    "warehouse_query_send_journal",
    "warehouse_query_provider_policies",
    "warehouse_query_request_envelopes",
    "warehouse_query_recovery_attestations",
    "warehouse_query_result_attestations",
    "warehouse_query_cancel_intents",
    "warehouse_query_cancel_attempt_boundaries",
    "warehouse_query_publication_retirement_fences",
)
# Sequences are granted only for INSERT tables whose columns may use defaults.
# Inspect catalog rather than giving blanket usage on all sequences.


def _role(conn):
    row=conn.execute("""
        SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,
               rolreplication,rolbypassrls,
               (SELECT count(*) FROM pg_auth_members m
                WHERE m.roleid=r.oid OR m.member=r.oid)
        FROM pg_roles r WHERE rolname=%s
    """,(CONTROL,)).fetchone()
    if row != (True,False,False,False,False,False,False,0):
        raise RuntimeError("UNSAFE_QUERY_CONTROL_ROLE")


def _catalog(conn):
    expected = {(s,t) for s,names in (
        ("public",PUBLIC_SELECT),(SCHEMA,QUERY_SELECT)
    ) for t in names}
    rows = conn.execute("""
        SELECT n.nspname,c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','p')
    """, (["public",SCHEMA],)).fetchall()
    found=set(rows)
    missing=expected-found
    if missing:
        raise RuntimeError("EXPECTED_CONTROL_TABLES_MISSING:"+",".join(
            sorted(f"{s}.{t}" for s,t in missing)))
    # Refuse drift in the isolated protocol namespace, including an
    # unexpectedly added query table not represented in the explicit manifest.
    extra={t for s,t in found if s==SCHEMA and t.startswith("warehouse_query_")} - set(QUERY_SELECT)
    if extra:
        raise RuntimeError("UNREVIEWED_QUERY_PROTOCOL_TABLE:"+",".join(sorted(extra)))


def _grant(conn, schema, table, privileges):
    conn.execute(sql.SQL("GRANT {} ON TABLE {}.{} TO {}").format(
        sql.SQL(", ").join(map(sql.SQL,privileges)),
        sql.Identifier(schema),sql.Identifier(table),sql.Identifier(CONTROL)
    ))


def _sequence_grants(conn):
    # Identity/serial sequences for exactly the allowed INSERT tables.
    tables=[("public",t) for t,w in PUBLIC_WRITES.items() if "INSERT" in w]
    tables += [(SCHEMA,t) for t,w in QUERY_WRITES.items() if "INSERT" in w]
    sequences=[]
    for schema,table in tables:
        rows=conn.execute("""
            SELECT DISTINCT sn.nspname, seq.relname
            FROM pg_depend d
            JOIN pg_class seq ON seq.oid=d.objid AND seq.relkind='S'
            JOIN pg_namespace sn ON sn.oid=seq.relnamespace
            JOIN pg_class parent ON parent.oid=d.refobjid
            JOIN pg_namespace pn ON pn.oid=parent.relnamespace
            WHERE pn.nspname=%s AND parent.relname=%s
              AND d.deptype IN ('a','i')
        """,(schema,table)).fetchall()
        for seq_schema,name in rows:
            if seq_schema!=schema:
                raise RuntimeError("SEQUENCE_OUTSIDE_REVIEWED_SCHEMA")
            sequences.append((seq_schema,name))
    for seq_schema,name in sorted(set(sequences)):
        conn.execute(sql.SQL("GRANT USAGE ON SEQUENCE {}.{} TO {}").format(
            sql.Identifier(seq_schema),sql.Identifier(name),sql.Identifier(CONTROL)
        ))
    return sorted(set(sequences))


def _assert_privileges(conn, seqs):
    for schema,names in (("public",PUBLIC_SELECT),(SCHEMA,QUERY_SELECT)):
        for table in names:
            privs=conn.execute("""
                SELECT has_table_privilege(%s,%s,'SELECT'),
                       has_table_privilege(%s,%s,'INSERT'),
                       has_table_privilege(%s,%s,'UPDATE'),
                       has_table_privilege(%s,%s,'DELETE'),
                       has_table_privilege(%s,%s,'TRUNCATE')
            """,(CONTROL,f"{schema}.{table}",CONTROL,f"{schema}.{table}",
                 CONTROL,f"{schema}.{table}",CONTROL,f"{schema}.{table}",
                 CONTROL,f"{schema}.{table}")).fetchone()
            expected_writes=(PUBLIC_WRITES if schema=="public" else QUERY_WRITES).get(table,())
            if privs != (
                True,"INSERT" in expected_writes,"UPDATE" in expected_writes,
                False,False
            ):
                raise RuntimeError("CONTROL_TABLE_PRIVILEGE_DRIFT:"+schema+"."+table)
    for schema,name in seqs:
        if not conn.execute("SELECT has_sequence_privilege(%s,%s,'USAGE')",
                            (CONTROL,f"{schema}.{name}")).fetchone()[0]:
            raise RuntimeError("CONTROL_SEQUENCE_GRANT_MISSING")
    if conn.execute("SELECT has_schema_privilege(%s,%s,'CREATE')",
                    (CONTROL,SCHEMA)).fetchone()[0]:
        raise RuntimeError("CONTROL_SCHEMA_CREATE_FORBIDDEN")
    if conn.execute("SELECT has_schema_privilege(%s,%s,'CREATE')",
                    (CONTROL,"public")).fetchone()[0]:
        raise RuntimeError("CONTROL_PUBLIC_SCHEMA_CREATE_FORBIDDEN")


def install(conn):
    if conn.execute("SELECT current_database(),session_user").fetchone() != (
        "ai_data_workspace","postgres"
    ):
        raise RuntimeError("LOCAL_DEVELOPMENT_ADMIN_REQUIRED")
    # This installer must only be run after the permanent cutover.
    verify_cutover(conn)
    _role(conn)
    _catalog(conn)
    for schema in ("public",SCHEMA):
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(schema),sql.Identifier(CONTROL)
        ))
    for table in PUBLIC_SELECT:
        _grant(conn,"public",table,("SELECT",)+PUBLIC_WRITES.get(table,()))
    for table in QUERY_SELECT:
        _grant(conn,SCHEMA,table,("SELECT",)+QUERY_WRITES.get(table,()))
    seqs=_sequence_grants(conn)
    _assert_privileges(conn,seqs)
    return {"query_control_role":CONTROL,"canonical_read_tables":len(PUBLIC_SELECT),
            "query_protocol_read_tables":len(QUERY_SELECT),
            "canonical_write_tables":sorted(PUBLIC_WRITES),
            "query_protocol_insert_tables":sorted(QUERY_WRITES),
            "sequence_usage_grants":len(seqs),"query_execution_enabled":False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    try:
        with psycopg.connect(_dsn(),autocommit=False) as conn:
            try:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                             ("datarise-wh-control-acl-install",))
                result=install(conn)
                if args.apply:
                    conn.commit()
                    result["transaction"]="APPLIED"
                else:
                    conn.rollback()
                    result["transaction"]="ROLLBACK_ONLY"
                print(json.dumps(result,sort_keys=True,indent=2))
                return 0
            except BaseException:
                conn.rollback()
                raise
    except Exception as exc:
        print("WAREHOUSE_CONTROL_ACL_BLOCKED:",type(exc).__name__,
              str(exc) if isinstance(exc,RuntimeError) else "",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
