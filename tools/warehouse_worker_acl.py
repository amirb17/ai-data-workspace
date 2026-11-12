"""Fail-closed database ACL installer for independent warehouse query workers.

All grants are constrained to explicit schema/table/operation tuples. No LOGIN
credentials, role memberships, search-path changes, or BigQuery operations are
created. The script defaults to rollback; --apply commits only after checking
the cutover and matching exact effective privileges. This is a DB boundary,
NOT a way to enable the HTTP API or combine worker roles in one process.

The control role is managed separately by tools.warehouse_control_acl.
Production RDS needs its own approved administrative provisioning.
"""
from __future__ import annotations

import argparse
import json
import sys

import psycopg
from psycopg import sql

from tools.warehouse_deployment_boundary import _dsn
from tools.warehouse_post_cutover_acceptance import inspect as verify_cutover

SCHEMA = "datarise_warehouse_dev"
CANONICAL = "public"
ROLES = {
    "provider_submission": "datarise_wh_submit",
    "recovery_attestation": "datarise_wh_attest",
    "accounting": "datarise_wh_account",
    "result_authorization": "datarise_wh_results",
}
# Qualified, explicit permissions per duty. No DELETE, TRUNCATE, DDL.
# Each worker has metadata reads needed for immutable job identity and
# bounded recovery. No worker can write another worker's attestation ledger.
_COMMON_READS = {
    ("public", "query_governance_reservations"),
    (SCHEMA, "warehouse_query_attempt_registry"),
    (SCHEMA, "warehouse_query_attempt_publication_pins"),
    (SCHEMA, "warehouse_query_attempt_reservation_links"),
    (SCHEMA, "warehouse_query_dispatch_checkpoints"),
    (SCHEMA, "warehouse_query_dispatch_revocations"),
    (SCHEMA, "warehouse_query_send_intents"),
    (SCHEMA, "warehouse_query_send_journal"),
}
READS = {
    "provider_submission": _COMMON_READS | {
        (SCHEMA, "warehouse_query_provider_policies"),
        (SCHEMA, "warehouse_query_request_envelopes"),
        (SCHEMA, "warehouse_query_result_contracts"),
    },
    "recovery_attestation": _COMMON_READS | {
        (SCHEMA, "warehouse_query_provider_policies"),
        (SCHEMA, "warehouse_query_request_envelopes"),
        (SCHEMA, "warehouse_query_recovery_attestations"),
        (SCHEMA, "warehouse_query_result_attestations"),
        (SCHEMA, "warehouse_query_cancel_intents"),
        (SCHEMA, "warehouse_query_cancel_attempt_boundaries"),
    },
    "accounting": _COMMON_READS | {
        ("public", "workspaces"),
        (SCHEMA, "warehouse_query_request_envelopes"),
        ("public", "query_governance_usage"),
        (SCHEMA, "warehouse_query_recovery_attestations"),
        (SCHEMA, "warehouse_query_result_attestations"),
        (SCHEMA, "warehouse_query_provider_policies"),
    },
    "result_authorization": _COMMON_READS | {
        ("public", "users"),
        ("public", "auth_sessions"),
        ("public", "workspaces"),
        ("public", "query_governance_usage"),
        (SCHEMA, "warehouse_query_result_contracts"),
        (SCHEMA, "warehouse_query_result_attestations"),
        (SCHEMA, "warehouse_query_provider_policies"),
        (SCHEMA, "warehouse_query_api_requests"),
        (SCHEMA, "warehouse_query_request_envelopes"),
        (SCHEMA, "warehouse_query_recovery_attestations"),
    },
}
WRITES = {
    "provider_submission": {
        (SCHEMA, "warehouse_query_send_journal"): ("INSERT",),
        (SCHEMA, "warehouse_query_request_envelopes"): ("INSERT",),
        (SCHEMA, "warehouse_query_cancel_intents"): ("INSERT",),
        (SCHEMA, "warehouse_query_cancel_attempt_boundaries"): ("INSERT",),
    },
    "recovery_attestation": {
        (SCHEMA, "warehouse_query_send_journal"): ("INSERT",),
        (SCHEMA, "warehouse_query_recovery_attestations"): ("INSERT",),
        (SCHEMA, "warehouse_query_result_attestations"): ("INSERT",),
    },
    "accounting": {
        ("public", "query_governance_reservations"): ("UPDATE",),
        ("public", "query_governance_usage"): ("INSERT",),
    },
    "result_authorization": {},
}
# Some writes (e.g. the shared journal) necessarily belong to more than one
# independent role, each requiring an exact lease/attempt guard in SQL.
FORBIDDEN = ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def _identity(conn, role):
    row = conn.execute("""
        SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,
               rolreplication,rolbypassrls,
               (SELECT count(*) FROM pg_auth_members m
                WHERE m.roleid=r.oid OR m.member=r.oid)
        FROM pg_roles r WHERE rolname=%s
    """, (role,)).fetchone()
    if row != (True, False, False, False, False, False, False, 0):
        raise RuntimeError("UNTRUSTED_WAREHOUSE_WORKER_ROLE:" + role)


def _tables(conn):
    expected=set().union(*READS.values(), *(set(x) for x in WRITES.values()))
    found=set(conn.execute("""
        SELECT n.nspname,c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','p')
    """, ([SCHEMA,CANONICAL],)).fetchall())
    missing=expected-found
    if missing:
        raise RuntimeError("WAREHOUSE_WORKER_MANIFEST_TABLE_MISSING:"+
                           ",".join(sorted(f"{a}.{b}" for a,b in missing)))


def _sequences(conn, allowed):
    """Grant sequence USAGE only when bound to a permitted INSERT table."""
    found=set()
    for schema,table in allowed:
        rows=conn.execute("""
            SELECT sn.nspname,seq.relname
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
                raise RuntimeError("WORKER_SEQUENCE_SCHEMA_DRIFT")
            found.add((seq_schema,name))
    return sorted(found)


def install(conn):
    if conn.execute("SELECT current_database(),session_user").fetchone() != (
        "ai_data_workspace","postgres"
    ):
        raise RuntimeError("LOCAL_DEV_ADMIN_REQUIRED")
    verify_cutover(conn)
    for role in ROLES.values():
        _identity(conn,role)
    _tables(conn)
    for duty,role in ROLES.items():
        for schema in (CANONICAL,SCHEMA):
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema),sql.Identifier(role)))
        for schema,table in sorted(READS[duty]):
            grants=("SELECT",)+WRITES[duty].get((schema,table),())
            conn.execute(sql.SQL("GRANT {} ON TABLE {}.{} TO {}").format(
                sql.SQL(", ").join(map(sql.SQL,grants)),
                sql.Identifier(schema),sql.Identifier(table),
                sql.Identifier(role)))
        # A write-only table needs explicit SELECT for RETURNING and identity
        # comparison, but not every writer is entitled to read all rows.
        for schema,table in sorted(set(WRITES[duty])-READS[duty]):
            rights=WRITES[duty][schema,table]
            conn.execute(sql.SQL("GRANT {} ON TABLE {}.{} TO {}").format(
                sql.SQL(", ").join(map(sql.SQL,rights)),
                sql.Identifier(schema),sql.Identifier(table),
                sql.Identifier(role)))
        seqs=_sequences(conn,[
            (schema,table) for (schema,table),rights in WRITES[duty].items()
            if "INSERT" in rights])
        for schema,name in seqs:
            conn.execute(sql.SQL("GRANT USAGE ON SEQUENCE {}.{} TO {}").format(
                sql.Identifier(schema),sql.Identifier(name),
                sql.Identifier(role)))
        _verify(conn,duty,role)
    return {
        "worker_roles": list(ROLES.values()),
        "read_table_counts": {d:len(r) for d,r in READS.items()},
        "write_table_counts": {d:len(w) for d,w in WRITES.items()},
        "query_execution_enabled": False,
        "worker_credentials_configured": False,
    }


def _verify(conn,duty,role):
    # Audit ALL tables in both schemas, including tables not in the manifest:
    # preexisting grants must not allow writes outside the approved duty.
    rows=conn.execute("""
        SELECT n.nspname,c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','p')
    """, ([CANONICAL,SCHEMA],)).fetchall()
    permitted=WRITES[duty]
    for schema,table in rows:
        name=f"{schema}.{table}"
        rights=conn.execute("""
            SELECT has_table_privilege(%s,%s,'INSERT'),
                   has_table_privilege(%s,%s,'UPDATE'),
                   has_table_privilege(%s,%s,'DELETE'),
                   has_table_privilege(%s,%s,'TRUNCATE'),
                   has_table_privilege(%s,%s,'REFERENCES'),
                   has_table_privilege(%s,%s,'TRIGGER')
        """, tuple(v for _ in range(6) for v in (role,name))).fetchone()
        expected=tuple(p in permitted.get((schema,table),()) for p in
                       ("INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER"))
        if rights!=expected:
            raise RuntimeError("UNEXPECTED_EFFECTIVE_WORKER_WRITE:"+duty+":"+name)
    for schema in (CANONICAL,SCHEMA):
        if conn.execute("SELECT has_schema_privilege(%s,%s,'CREATE')",
                        (role,schema)).fetchone()[0]:
            raise RuntimeError("UNEXPECTED_WORKER_SCHEMA_CREATE:"+duty)
    for schema,table in READS[duty]:
        if not conn.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
                            (role,f"{schema}.{table}")).fetchone()[0]:
            raise RuntimeError("MISSING_WORKER_READ:"+duty+":"+table)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    try:
        with psycopg.connect(_dsn(),autocommit=False) as conn:
            try:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                             ("datarise-warehouse-workers-acl",))
                report=install(conn)
                if args.apply:
                    conn.commit()
                    report["transaction"]="APPLIED"
                else:
                    conn.rollback()
                    report["transaction"]="ROLLBACK_ONLY"
                print(json.dumps(report,indent=2,sort_keys=True))
                return 0
            except BaseException:
                conn.rollback()
                raise
    except Exception as exc:
        print("WAREHOUSE_WORKER_ACL_BLOCKED:",type(exc).__name__,
              str(exc) if isinstance(exc,RuntimeError) else "",
              file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
