"""Independent real-login acceptance for one warehouse DB duty per process.

Each duty is a different OS process and its own direct-login credential. This
command neither installs credentials nor activates warehouse queries. The DSN
is passed via DATARISE_WAREHOUSE_DUTY_DSN in the worker environment and never
logged or included in results. It refuses a default DATABASE_URL or an admin
identity. Run the command separately in the actual deployment environment of
each worker; never share a principal or call every duty within one process.

  python -m tools.warehouse_runtime_role_acceptance query_control
  python -m tools.warehouse_runtime_role_acceptance provider_submission
  python -m tools.warehouse_runtime_role_acceptance recovery_attestation
  python -m tools.warehouse_runtime_role_acceptance accounting
  python -m tools.warehouse_runtime_role_acceptance result_authorization

Local-only DB name is pinned by the pre-existing factory. Production RDS
requires a separately reviewed factory and installation design.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from app.db.warehouse_canonical_query_connection import (
    CanonicalWarehouseQueryControlConnection,
    verify_control_write_boundary,
    verify_scope,
)
from app.db.warehouse_restricted_connection import (
    RestrictedWarehouseConnection, ROLE_BY_DUTY,
)
from tools.warehouse_worker_acl import READS, WRITES

DUTIES = (
    "query_control", "provider_submission", "recovery_attestation",
    "accounting", "result_authorization",
)
_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def _table_privileges(conn, role: str, qualified: str) -> dict[str, bool]:
    # Catalog API accepts only a qualified catalog object name and a fixed
    # privilege set. Neither SQL identifiers nor user SQL are interpolated.
    query = "SELECT " + ", ".join(
        "has_table_privilege(%s,%s,%s)" for _ in _PRIVILEGES
    )
    bindings = tuple(value for op in _PRIVILEGES for value in (role, qualified, op))
    values = conn.execute(query, bindings).fetchone()
    if values is None or len(values) != len(_PRIVILEGES):
        raise RuntimeError("ROLE_GRANT_INSPECTION_UNAVAILABLE")
    return dict(zip(_PRIVILEGES, values))


def _check_worker(conn, duty):
    role = ROLE_BY_DUTY[duty]
    allowed_reads = READS[duty]
    allowed_writes = WRITES[duty]
    required = allowed_reads | set(allowed_writes)
    seen = set()
    rows = conn.execute("""
        SELECT n.nspname, c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','p')
    """, (["public", "datarise_warehouse_dev"],)).fetchall()
    for schema, table in rows:
        target = (schema, table)
        permissions = _table_privileges(conn, role, f"{schema}.{table}")
        needed = set(allowed_writes.get(target, ()))
        if target in allowed_reads:
            needed.add("SELECT")
        # Extra read access is also forbidden: these logins must not inspect
        # unrelated application accounts, source data or provider secrets.
        if {op for op, allowed in permissions.items() if allowed} != needed:
            raise RuntimeError("ROLE_EFFECTIVE_TABLE_GRANTS_DRIFT")
        if needed:
            seen.add(target)
    if seen != required:
        raise RuntimeError("ROLE_REQUIRED_TABLES_NOT_VISIBLE")
    for schema in ("public", "datarise_warehouse_dev"):
        privilege = conn.execute(
            "SELECT has_schema_privilege(%s,%s,'CREATE')", (role, schema)
        ).fetchone()
        if privilege is None or privilege[0] is not False:
            raise RuntimeError("WORKER_SCHEMA_CREATE_FORBIDDEN")
    return {"verified_tables": len(seen), "read_tables": len(allowed_reads),
            "write_tables": len(allowed_writes)}


def accept(duty: str, dsn: str) -> dict:
    if duty not in DUTIES or not isinstance(dsn, str) or not dsn:
        raise ValueError("EXPLICIT_SINGLE_DUTY_DSN_REQUIRED")
    if duty == "query_control":
        factory = CanonicalWarehouseQueryControlConnection(dsn)
    else:
        factory = RestrictedWarehouseConnection(
            duty=duty, dsn=dsn, login=ROLE_BY_DUTY[duty]
        )
    with factory() as conn:
        if duty == "query_control":
            verify_scope(conn)
            verify_control_write_boundary(conn)
            return {"duty": duty, "role": ROLE_BY_DUTY[duty],
                    "canonical_scope": True, "effective_write_scope": True,
                    "query_execution_enabled": False}
        result = _check_worker(conn, duty)
        return {"duty": duty, "role": ROLE_BY_DUTY[duty], **result,
                "query_execution_enabled": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("duty", choices=DUTIES)
    args = parser.parse_args()
    dsn = os.environ.get("DATARISE_WAREHOUSE_DUTY_DSN")
    if not dsn:
        print("WAREHOUSE_ROLE_ACCEPTANCE_BLOCKED: EXPLICIT_DUTY_DSN_MISSING",
              file=sys.stderr)
        return 2
    try:
        report = accept(args.duty, dsn)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        # Never print connection exceptions: they can include the DSN.
        print("WAREHOUSE_ROLE_ACCEPTANCE_BLOCKED:", type(exc).__name__,
              str(exc) if type(exc) in (ValueError, RuntimeError) else "",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
