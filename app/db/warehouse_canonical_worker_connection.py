"""Canonical + query-protocol routing for an independent restricted worker.

A worker must read real canonical public reservations, not empty isolated
copies, while query-protocol tables remain in datarise_warehouse_dev.
This adapter preserves a single restricted direct-login role per OS process.

The startup real-login acceptance separately verifies the exact table ACL
manifest. Here each new connection verifies its role, no public CREATE, no
public query-table shadow, and the deterministic search_path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from psycopg.pq import TransactionStatus

from app.db.database import connection_boundary
from app.db.warehouse_restricted_connection import (
    RestrictedWarehouseConnection, ROLE_BY_DUTY,
)

ISOLATED = "datarise_warehouse_dev"
SEARCH_PATH = "pg_catalog, public, datarise_warehouse_dev, pg_temp"
WORKER_DUTIES = frozenset({
    "provider_submission", "recovery_attestation",
    "accounting", "result_authorization",
})


def verify_canonical_routing(conn, duty):
    if duty not in WORKER_DUTIES:
        raise RuntimeError("CANONICAL_WORKER_DUTY_FORBIDDEN")
    row=conn.execute("""
        SELECT current_user,session_user,current_database(),
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
    if (row is None or row[0] != ROLE_BY_DUTY[duty]
            or row[1] != ROLE_BY_DUTY[duty] or row[2] != "ai_data_workspace"
            or row[3] is not True or row[4] is not False or row[5] is not False):
        raise RuntimeError("CANONICAL_WORKER_ROUTING_UNTRUSTED")
    shadow=conn.execute("""
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname LIKE 'warehouse_query_%'
          AND c.relkind IN ('r','p','v','m','f','S') LIMIT 1
    """).fetchone()
    if shadow:
        raise RuntimeError("PUBLIC_QUERY_PROTOCOL_SHADOWING")


@dataclass(frozen=True)
class CanonicalWarehouseWorkerConnection:
    duty: str
    dsn: str = field(repr=False)

    def __post_init__(self):
        if self.duty not in WORKER_DUTIES:
            raise ValueError("UNSUPPORTED_WAREHOUSE_WORKER_DUTY")
        # This validates the DSN user and local database eagerly.
        RestrictedWarehouseConnection(
            duty=self.duty,dsn=self.dsn,login=ROLE_BY_DUTY[self.duty])

    def __call__(self):
        restricted=RestrictedWarehouseConnection(
            duty=self.duty,dsn=self.dsn,login=ROLE_BY_DUTY[self.duty])
        conn=restricted()
        try:
            verify_canonical_routing(conn,self.duty)
            conn.execute("SELECT set_config('search_path',%s,false)",
                         (SEARCH_PATH,))
            conn.commit()
            if conn.info.transaction_status!=TransactionStatus.IDLE:
                raise RuntimeError("WORKER_CANONICAL_PATH_NOT_IDLE")
            actual=conn.execute("SELECT current_schemas(true)").fetchone()[0]
            if tuple(actual[:3]) != ("pg_catalog","public",ISOLATED):
                raise RuntimeError("WORKER_CANONICAL_PATH_MISMATCH")
            conn.rollback()
            return conn
        except BaseException:
            conn.close()
            raise

    def boundary(self):
        return connection_boundary(self.duty,self)
