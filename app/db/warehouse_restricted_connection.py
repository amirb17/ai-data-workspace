"""Fail-closed PostgreSQL factory for a separately provisioned warehouse worker.

This module DOES NOT grant permissions, create credentials, choose principals,
switch roles, enable the query HTTP dependency, or make multiple duties hosted
in one process independently isolated. Provision one restricted login per
independent process and pass that process only its single approved DSN.

The live warehouse query runtime remains disabled until the database ACL matrix,
trigger closure, duty placement and real-login acceptance are reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import os
from threading import Lock

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus

from app.db.database import connection_boundary

SCHEMA = "datarise_warehouse_dev"
ROLE_BY_DUTY = {
    "query_control": "datarise_wh_control",
    "provider_submission": "datarise_wh_submit",
    "recovery_attestation": "datarise_wh_attest",
    "accounting": "datarise_wh_account",
    "result_authorization": "datarise_wh_results",
    "publication": "datarise_wh_publish",
    "retention_inspection": "datarise_wh_retention",
}
DUTIES = frozenset(ROLE_BY_DUTY)
_FORBIDDEN_MEMBERSHIPS = (
    "datarise_wh_owner",
    "datarise_authority_owner",
    "datarise_api",
    "datarise_worker",
    "datarise_verifier",
    "datarise_dispatch",
    "pg_read_all_data",
    "pg_write_all_data",
    "pg_execute_server_program",
    "pg_read_server_files",
    "pg_write_server_files",
    "pg_monitor",
    "pg_signal_backend",
    "pg_create_subscription",
)
_NAME = re.compile(r"datarise_[a-z][a-z0-9_]{2,62}\Z")


_PROCESS_LOCK = Lock()
_PROCESS_BINDING = None


def _bind_process(duty: str, login: str):
    """One OS process may hold credentials for only one warehouse DB duty.

    Separate worker processes still require independent credential injection;
    this detects accidental in-process role switching.
    """
    global _PROCESS_BINDING
    identity = (os.getpid(), duty, login)
    with _PROCESS_LOCK:
        if _PROCESS_BINDING is None:
            _PROCESS_BINDING = identity
        elif _PROCESS_BINDING != identity:
            raise RuntimeError("CROSS_DUTY_WAREHOUSE_PROCESS_REUSE_FORBIDDEN")


@dataclass(frozen=True)
class RestrictedWarehouseConnection:
    """Explicit DSN and expected login: never infer them from DATABASE_URL.

    This factory can be used by exactly one duty/process. Do not combine
    factories for submission, attestation and accounting in a single worker.
    """

    duty: str
    dsn: str = field(repr=False)
    login: str

    def __post_init__(self):
        if self.duty not in DUTIES or type(self.dsn) is not str or not self.dsn:
            raise ValueError("EXPLICIT_RESTRICTED_WAREHOUSE_DSN_REQUIRED")
        if (type(self.login) is not str or not _NAME.fullmatch(self.login)
                or self.login != ROLE_BY_DUTY[self.duty]):
            raise ValueError("RESTRICTED_WAREHOUSE_LOGIN_REQUIRED")
        # Avoid accidental local admin/role reuse and refuse URI-based credentials
        # that do not name the intended direct-login role.
        settings = conninfo_to_dict(self.dsn)
        if settings.get("user") != self.login or settings.get("dbname") != "ai_data_workspace":
            raise ValueError("RESTRICTED_WAREHOUSE_CONNECTION_IDENTITY_MISMATCH")
        if settings.get("options"):
            raise ValueError("UNREVIEWED_CONNECTION_OPTIONS")

    def __call__(self):
        _bind_process(self.duty, self.login)
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
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_auth_members m
                        WHERE m.member=(SELECT oid FROM pg_roles WHERE rolname=current_user)
                           OR m.roleid=(SELECT oid FROM pg_roles WHERE rolname=current_user)
                    )
                """)
                if cur.fetchone()[0]:
                    raise RuntimeError("WAREHOUSE_RUNTIME_ROLE_MEMBERSHIP_FORBIDDEN")
                cur.execute("SELECT has_schema_privilege(current_user,%s,'USAGE'),"
                            "has_schema_privilege(current_user,%s,'CREATE')",
                            (SCHEMA, SCHEMA))
                usage, create = cur.fetchone()
                if not usage or create:
                    raise RuntimeError("WAREHOUSE_SCHEMA_PRIVILEGE_BOUNDARY_INVALID")
                cur.execute("SELECT current_user = pg_get_userbyid(nspowner) "
                            "FROM pg_namespace WHERE nspname=%s", (SCHEMA,))
                if cur.fetchone()[0] is not False:
                    raise RuntimeError("WAREHOUSE_RUNTIME_SCHEMA_OWNERSHIP_FORBIDDEN")
                cur.execute("SELECT current_setting('search_path')")
                # SQL containing unqualified names must not resolve through public.
                cur.execute("SELECT set_config('search_path', %s, false)",
                            (f"{SCHEMA}, pg_catalog, pg_temp",))
                cur.execute("SELECT current_schema()")
                if cur.fetchone()[0] != SCHEMA:
                    raise RuntimeError("RESTRICTED_WAREHOUSE_SEARCH_PATH_MISMATCH")
            # set_config(..., false) is a SESSION setting, but rolling back
            # the transaction that issued it can restore the original value.
            # Commit the setup before handing the connection to repositories.
            conn.commit()
            if conn.info.transaction_status != TransactionStatus.IDLE:
                raise RuntimeError("RESTRICTED_CONNECTION_NOT_IDLE_AFTER_SETUP")
            with conn.cursor() as cur:
                cur.execute("SELECT current_schema()")
                if cur.fetchone()[0] != SCHEMA:
                    raise RuntimeError("RESTRICTED_WAREHOUSE_SEARCH_PATH_DID_NOT_PERSIST")
            conn.rollback()
            return conn
        except BaseException:
            conn.close()
            raise

    def boundary(self):
        """Call only inside the worker dedicated to this one duty."""
        return connection_boundary(self.duty, self)
