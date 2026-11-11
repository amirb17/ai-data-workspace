"""Fail-closed PostgreSQL factory for a separately provisioned warehouse worker.

This module DOES NOT grant permissions, create credentials, choose principals,
switch roles, enable the query HTTP dependency, or make multiple duties hosted
in one process independently isolated. Provision one restricted login per
independent process and pass that process only its single approved DSN.

The live warehouse query runtime remains disabled until the database ACL matrix,
trigger closure, duty placement and real-login acceptance are reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus

from app.db.database import connection_boundary

SCHEMA = "datarise_warehouse_dev"
DUTIES = frozenset({
    "query_control",
    "provider_submission",
    "recovery_attestation",
    "accounting",
    "result_authorization",
    "publication",
    "retention_inspection",
})
_NAME = re.compile(r"datarise_[a-z][a-z0-9_]{2,62}\Z")


@dataclass(frozen=True)
class RestrictedWarehouseConnection:
    """Explicit DSN and expected login: never infer them from DATABASE_URL.

    This factory can be used by exactly one duty/process. Do not combine
    factories for submission, attestation and accounting in a single worker.
    """

    duty: str
    dsn: str
    login: str

    def __post_init__(self):
        if self.duty not in DUTIES or type(self.dsn) is not str or not self.dsn:
            raise ValueError("EXPLICIT_RESTRICTED_WAREHOUSE_DSN_REQUIRED")
        if type(self.login) is not str or not _NAME.fullmatch(self.login):
            raise ValueError("RESTRICTED_WAREHOUSE_LOGIN_REQUIRED")
        # Avoid accidental local admin/role reuse and refuse URI-based credentials
        # that do not name the intended direct-login role.
        settings = conninfo_to_dict(self.dsn)
        if settings.get("user") != self.login or settings.get("dbname") != "ai_data_workspace":
            raise ValueError("RESTRICTED_WAREHOUSE_CONNECTION_IDENTITY_MISMATCH")
        if settings.get("options"):
            raise ValueError("UNREVIEWED_CONNECTION_OPTIONS")

    def __call__(self):
        conn = psycopg.connect(self.dsn, autocommit=False)
        try:
            if conn.info.transaction_status != TransactionStatus.IDLE:
                raise RuntimeError("RESTRICTED_CONNECTION_NOT_IDLE")
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT current_user, session_user, current_database(),
                           rolcanlogin, rolsuper, rolcreaterole, rolcreatedb,
                           rolreplication, rolbypassrls,
                           pg_has_role(current_user,'pg_read_all_data','MEMBER'),
                           pg_has_role(current_user,'pg_write_all_data','MEMBER')
                    FROM pg_roles WHERE rolname=current_user
                """)
                row = cur.fetchone()
                if (row is None or row[0] != self.login or row[1] != self.login
                        or row[2] != "ai_data_workspace" or row[3] is not True
                        or any(row[4:])):
                    raise RuntimeError("RESTRICTED_DATABASE_LOGIN_NOT_VERIFIED")
                cur.execute("SELECT has_schema_privilege(current_user, %s, 'USAGE')",
                            (SCHEMA,))
                if cur.fetchone()[0] is not True:
                    raise RuntimeError("RESTRICTED_WAREHOUSE_SCHEMA_ACCESS_MISSING")
                cur.execute("SELECT current_setting('search_path')")
                # SQL containing unqualified names must not resolve through public.
                cur.execute("SELECT set_config('search_path', %s, false)",
                            (f"{SCHEMA}, pg_catalog, pg_temp",))
                cur.execute("SELECT current_schema()")
                if cur.fetchone()[0] != SCHEMA:
                    raise RuntimeError("RESTRICTED_WAREHOUSE_SEARCH_PATH_MISMATCH")
            # Do not return a connection with an open read-only verification TX.
            conn.rollback()
            return conn
        except BaseException:
            conn.close()
            raise

    def boundary(self):
        """Call only inside the worker dedicated to this one duty."""
        return connection_boundary(self.duty, self)
