"""Restricted query-control connection spanning canonical and query schemas.

The public schema contains the real workspace/session/governance/warehouse
records. The isolated schema contains ONLY warehouse query protocol records.
An API/worker with a dedicated control login needs both, without a superuser
connection or copies of authentication data.

No implicit DSN, grants, cross-duty process reuse, SET ROLE or cloud transport.
Only the query_control login may use this factory. It refuses public CREATE
authority or shadow query objects that could divert unqualified SQL.

Not a standalone activation switch. Post-cutover FKs, trigger/ACL closure and
genuine negative authorization tests must pass before production enablement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from psycopg.pq import TransactionStatus

from app.db.database import connection_boundary
from app.db.warehouse_restricted_connection import RestrictedWarehouseConnection

ISOLATED = "datarise_warehouse_dev"
CANONICAL = "public"
SEARCH_PATH = "pg_catalog, public, datarise_warehouse_dev, pg_temp"
REQUIRED_PUBLIC_READS = (
    "users", "auth_sessions", "workspaces", "datasets",
    "query_governance_reservations", "query_governance_usage",
    "warehouse_models", "warehouse_model_versions",
    "warehouse_model_sources", "warehouse_model_configurations",
    "warehouse_resources", "warehouse_publications",
    "warehouse_publication_heads", "warehouse_serving_materializations",
)
REQUIRED_ISOLATED = (
    "warehouse_query_attempt_registry",
    "warehouse_query_dispatch_checkpoints",
    "warehouse_query_api_requests",
    "warehouse_query_result_contracts",
)


def verify_scope(conn):
    """Fail-closed catalog checks before a query-control transaction is lent."""
    row = conn.execute("""
        SELECT current_user, session_user, current_database(),
               has_schema_privilege(current_user,'public','USAGE'),
               has_schema_privilege(current_user,'public','CREATE'),
               EXISTS (
                   SELECT 1 FROM pg_namespace n
                   CROSS JOIN LATERAL aclexplode(
                       coalesce(n.nspacl,acldefault('n',n.nspowner))
                   ) a
                   WHERE n.nspname='public' AND a.grantee=0
                     AND a.privilege_type='CREATE'
               )
    """).fetchone()
    if (row is None or row[0] != "datarise_wh_control"
            or row[1] != "datarise_wh_control"
            or row[2] != "ai_data_workspace"
            or row[3] is not True or row[4] is not False
            or row[5] is not False):
        raise RuntimeError("CANONICAL_QUERY_CONTROL_SCOPE_UNTRUSTED")

    shadow = conn.execute("""
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname LIKE 'warehouse_query_%'
          AND c.relkind IN ('r','p','v','m','f','S')
        LIMIT 1
    """).fetchone()
    if shadow:
        raise RuntimeError("PUBLIC_QUERY_PROTOCOL_NAME_SHADOWING")

    for table in REQUIRED_PUBLIC_READS:
        allowed = conn.execute(
            "SELECT has_table_privilege(current_user,%s,'SELECT')",
            (f"public.{table}",),
        ).fetchone()[0]
        if allowed is not True:
            raise RuntimeError("CANONICAL_READ_PRIVILEGE_MISSING")
    for table in REQUIRED_ISOLATED:
        exists = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL",
            (f"{ISOLATED}.{table}",),
        ).fetchone()[0]
        if exists is not True:
            raise RuntimeError("ISOLATED_QUERY_PROTOCOL_INCOMPLETE")


def verify_control_write_boundary(conn):
    """Reject accidental cross-duty writes before issuing API query transactions.

    The control login is trusted to admit a query, not to send or attest it.
    Effective grants (including PUBLIC grants) are checked, not just explicit
    direct grants. No application data is read by this check.
    """
    expected = {
        ("public", "query_governance_reservations"): ("INSERT", "UPDATE"),
        ("datarise_warehouse_dev", "warehouse_query_attempt_registry"): ("INSERT",),
        ("datarise_warehouse_dev", "warehouse_query_attempt_logical_drafts"): ("INSERT",),
        ("datarise_warehouse_dev", "warehouse_query_attempt_publication_pins"): ("INSERT",),
        ("datarise_warehouse_dev", "warehouse_query_attempt_reservation_links"): ("INSERT",),
        ("datarise_warehouse_dev", "warehouse_query_api_requests"): ("INSERT",),
        ("datarise_warehouse_dev", "warehouse_query_result_contracts"): ("INSERT",),
        ("datarise_warehouse_dev", "warehouse_query_dispatch_checkpoints"): ("INSERT",),
        ("datarise_warehouse_dev", "warehouse_query_send_intents"): ("INSERT",),
        ("datarise_warehouse_dev", "warehouse_query_provider_policies"): ("INSERT",),
        ("datarise_warehouse_dev", "warehouse_query_request_envelopes"): ("INSERT",),
    }
    forbidden = (
        ("public", "users"),
        ("public", "auth_sessions"),
        ("public", "warehouse_publications"),
        ("public", "warehouse_model_versions"),
        ("datarise_warehouse_dev", "warehouse_query_send_journal"),
        ("datarise_warehouse_dev", "warehouse_query_recovery_attestations"),
        ("datarise_warehouse_dev", "warehouse_query_result_attestations"),
        ("datarise_warehouse_dev", "warehouse_query_dispatch_revocations"),
    )
    for schema, table in tuple(expected) + forbidden:
        wanted = set(expected.get((schema, table), ()))
        actual = conn.execute("""
            SELECT has_table_privilege(current_user,%s,'INSERT'),
                   has_table_privilege(current_user,%s,'UPDATE'),
                   has_table_privilege(current_user,%s,'DELETE'),
                   has_table_privilege(current_user,%s,'TRUNCATE')
        """, (f"{schema}.{table}",) * 4).fetchone()
        if actual != tuple(kind in wanted for kind in
                           ("INSERT", "UPDATE", "DELETE", "TRUNCATE")):
            raise RuntimeError("QUERY_CONTROL_EFFECTIVE_WRITE_GRANT_DRIFT")


@dataclass(frozen=True)
class CanonicalWarehouseQueryControlConnection:
    """Supply only to a dedicated *query-control* process and no other duty."""

    dsn: str = field(repr=False)

    def __call__(self):
        restricted = RestrictedWarehouseConnection(
            duty="query_control", dsn=self.dsn, login="datarise_wh_control"
        )
        conn = restricted()
        try:
            verify_scope(conn)
            verify_control_write_boundary(conn)
            conn.execute(
                "SELECT set_config('search_path', %s, false)", (SEARCH_PATH,)
            )
            conn.commit()
            if conn.info.transaction_status != TransactionStatus.IDLE:
                raise RuntimeError("CONTROL_SEARCH_PATH_SETUP_NOT_IDLE")
            resolved = conn.execute("SELECT current_schemas(true)").fetchone()[0]
            # pg_temp may be absent until temp tables exist; the first three
            # names are deterministic. PostgreSQL catalogs remain trusted.
            if tuple(resolved[:3]) != (
                "pg_catalog", "public", ISOLATED
            ):
                raise RuntimeError("CANONICAL_CONTROL_SEARCH_PATH_MISMATCH")
            conn.rollback()
            return conn
        except BaseException:
            conn.close()
            raise

    def boundary(self):
        return connection_boundary("query_control", self)
