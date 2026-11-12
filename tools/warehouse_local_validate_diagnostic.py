"""Read-only exact warehouse validation diagnostic for the local workspace.

Runs the same owned-model resolution, published serving metadata verification
and BigQuery dry-run estimate as /warehouse/queries/validate. Never admits a
query, changes database state, dispatches a BigQuery job, or prints SQL,
credentials, owner details or physical resource IDs.

    python -m tools.warehouse_local_validate_diagnostic
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from dotenv import load_dotenv
from fastapi import HTTPException
from psycopg import connect
from psycopg.rows import dict_row


def _code(exc):
    from app.processing.sql_validation import SqlBlocked
    if isinstance(exc, SqlBlocked):
        return "SQL_" + exc.code
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return detail if isinstance(detail, str) and re.fullmatch(
            r"[A-Z][A-Z0-9_]{4,79}", detail) else "HTTP_" + str(exc.status_code)
    value = str(exc) if isinstance(exc, ValueError) else ""
    if re.fullmatch(r"[A-Z][A-Z0-9_]{4,79}", value):
        return value
    enum = getattr(exc, "code", None)
    if enum is not None and re.fullmatch(r"[A-Z][A-Z0-9_]{4,79}", str(
        getattr(enum, "name", enum))):
        return str(getattr(enum, "name", enum))
    return type(exc).__name__


def check():
    from app.db import database
    from app.services import warehouse_query_workflow_service as workflow
    from tools.warehouse_local_live_api import (
        _assert_local_development, _bind_canonical_local_database, make_runtime,
    )
    root_env = Path(__file__).resolve().parents[1] / ".env"
    fallback = Path("D:/Project/ai-data-workspace/.env")
    if root_env.is_file():
        load_dotenv(root_env)
    elif fallback.is_file():
        load_dotenv(fallback)

    stages = {"database": "NOT_CHECKED", "model_resolution": "NOT_CHECKED",
              "provider_preflight": "NOT_CHECKED"}
    try:
        _assert_local_development()
        _bind_canonical_local_database()
        with connect(database.DATABASE_URL, row_factory=dict_row) as conn:
            # Application ownership checks require SELECT FOR SHARE, which
            # PostgreSQL rejects in a READ ONLY transaction. This code issues
            # only SELECTs plus cloud metadata/dry-run calls, and rolls back.
            with conn.transaction():
                scope = conn.execute(
                    "SELECT owner,status FROM public.workspaces WHERE workspace_id=18"
                ).fetchone()
                if not scope or scope["status"] != "ACTIVE":
                    stages["database"] = "WORKSPACE_NOT_ACTIVE"
                    return stages
                stages["database"] = "OK"
                try:
                    resolved = workflow._resolve(
                        conn, {"owner_key": scope["owner"]}, 18, 1,
                        'SELECT "customer_id" FROM model_1 LIMIT 100',
                    )
                except Exception as exc:
                    stages["model_resolution"] = _code(exc)
                    return stages
                stages["model_resolution"] = "OK"
                # Metadata is read-only and the BigQuery estimate is a dry run.
                try:
                    runtime = make_runtime()
                    workflow._preflight(runtime, resolved, 18)
                except Exception as exc:
                    stages["provider_preflight"] = _code(exc)
                    return stages
                stages["provider_preflight"] = "OK"
                return stages
    except Exception as exc:
        stages["database"] = _code(exc)
        return stages


def main():
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("AUTH_MODE", "session")
    os.environ.setdefault("DATARISE_PUBLIC_LAUNCH_MODE", "off")
    print(json.dumps(check(), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
