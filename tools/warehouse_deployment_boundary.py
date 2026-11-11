"""Read-only acceptance of the *actual* local warehouse deployment boundary.

Answers whether public and isolated schema contain the workspace-18 records,
and whether dedicated PostgreSQL warehouse roles exist with correct identities.
This tool never provisions roles, changes search_path globally, migrates auth
data, submits a query, retrieves tokens, or prints a connection string.

Usage: python -m tools.warehouse_deployment_boundary --workspace 18

The real application/workspace source of truth may be public, and the empty
isolated schema is not grounds to copy secrets or auth sessions into it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg
from psycopg import sql
from dotenv import dotenv_values

from app.db.warehouse_restricted_connection import ROLE_BY_DUTY

SCHEMAS = ("public", "datarise_warehouse_dev")
_TABLES = (
    "users",
    "auth_sessions",
    "workspaces",
    "warehouse_models",
    "warehouse_publications",
    "warehouse_serving_materializations",
    "warehouse_query_attempt_registry",
    "warehouse_query_result_contracts",
)
_NEED = (
    "users",
    "auth_sessions",
    "workspaces",
    "warehouse_models",
    "warehouse_publications",
    "warehouse_serving_materializations",
)


def _dsn():
    """Existing project configuration, without echoing values."""
    value = os.getenv("DATABASE_URL")
    if value:
        return value
    # The original checkout stores local PostgreSQL settings here.
    for path in (".env", "D:/Project/ai-data-workspace/.env"):
        if os.path.isfile(path):
            value = dotenv_values(path).get("DATABASE_URL")
            if value:
                return value
    raise RuntimeError("DATABASE_URL_NOT_CONFIGURED")


def _relation(conn, schema: str, name: str) -> bool:
    return conn.execute(
        """SELECT EXISTS (
          SELECT 1 FROM pg_catalog.pg_class c
          JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=%s AND c.relname=%s AND c.relkind IN ('r','p')
        )""", (schema, name)
    ).fetchone()[0]


def _count(conn, schema: str, name: str, workspace_id: int | None = None):
    # Both names come from hardcoded trusted constants, never user input.
    query = sql.SQL("SELECT count(*) FROM {}.{}").format(
        sql.Identifier(schema), sql.Identifier(name)
    )
    if workspace_id is not None:
        query += sql.SQL(" WHERE workspace_id = %s")
    return conn.execute(query, () if workspace_id is None else (workspace_id,)).fetchone()[0]


def inspect(conn, workspace_id: int) -> dict:
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("POSITIVE_WORKSPACE_REQUIRED")
    schemas = {}
    for schema in SCHEMAS:
        exists = conn.execute(
            "SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname=%s",
            (schema,),
        ).fetchone() is not None
        present = sorted(t for t in _TABLES if exists and _relation(conn, schema, t))
        data = {"exists": exists, "tables_present": present}
        if exists and all(t in present for t in _NEED):
            data["users"] = _count(conn, schema, "users")
            data["sessions"] = _count(conn, schema, "auth_sessions")
            data["workspace_found"] = (
                _count(conn, schema, "workspaces", workspace_id) > 0
            )
            data["model_count"] = _count(
                conn, schema, "warehouse_models", workspace_id
            )
            # Avoid assuming publication/receipt rows have a workspace_id;
            # the model join is resolved through the application repository.
            data["publication_rows_total"] = _count(
                conn, schema, "warehouse_publications"
            )
            data["materialization_rows_total"] = _count(
                conn, schema, "warehouse_serving_materializations"
            )
        schemas[schema] = data

    roles = {}
    for duty, role in ROLE_BY_DUTY.items():
        row = conn.execute(
            """SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb,
                      rolreplication, rolbypassrls, rolinherit,
                      (SELECT count(*) FROM pg_catalog.pg_auth_members m
                       WHERE m.member=r.oid OR m.roleid=r.oid)
               FROM pg_catalog.pg_roles r WHERE rolname=%s""",
            (role,),
        ).fetchone()
        roles[duty] = {
            "exists": row is not None,
            "safe_attributes": bool(
                row is not None and row[0] and not any(row[1:7])
                and row[7] == 0
            ),
            "has_schema_usage": bool(
                row is not None and conn.execute(
                    "SELECT has_schema_privilege(%s,%s,'USAGE')",
                    (role, "datarise_warehouse_dev"),
                ).fetchone()[0]
            ) if schemas["datarise_warehouse_dev"]["exists"] else False,
        }
    live = schemas["public"]
    dedicated = schemas["datarise_warehouse_dev"]
    if live.get("workspace_found") and not dedicated.get("workspace_found"):
        decision = "PUBLIC_IS_CURRENT_WORKSPACE_SOURCE; DO_NOT_SWITCH_API_TO_EMPTY_SCHEMA"
    elif dedicated.get("workspace_found") and not live.get("workspace_found"):
        decision = "DEDICATED_SCHEMA_WORKSPACE_PRESENT; REVIEW_AUTH_PROVENANCE"
    elif dedicated.get("workspace_found") and live.get("workspace_found"):
        decision = "TWO_WORKSPACE_SOURCES; REQUIRE_EXPLICIT_CANONICAL_OWNERSHIP"
    else:
        decision = "WORKSPACE_NOT_FOUND_IN_EITHER_SCHEMA"
    return {
        "workspace_id": workspace_id,
        "schemas": schemas,
        "restricted_roles": roles,
        "decision": decision,
        "warehouse_runtime_live_approved": False,
        "bigquery_app_e2e_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=int, default=18)
    args = parser.parse_args()
    try:
        # Read-only transaction prevents mutating the database even if this
        # file is accidentally extended with a write later.
        with psycopg.connect(_dsn()) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            result = inspect(conn, args.workspace)
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print("WAREHOUSE_DEPLOYMENT_INSPECTION_FAILED:",
              type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
