"""One-command local acceptance for authenticated warehouse query APIs.

Runs the existing API lifecycle, named-result and safety tests with isolated
disposable PostgreSQL schemas. Uses the repository's established test runner,
which blocks external cloud and AWS network calls. It does NOT exercise live
BigQuery or approve production credentials, roles or runtime activation.

Usage:
    python tools/warehouse_app_acceptance.py
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "tests/test_warehouse_query_workflow_api.py",
    "tests/test_warehouse_named_results_c2.py",
    "tests/test_warehouse_p0_named_results_http.py",
    "tests/test_warehouse_result_readiness_api_414g.py",
    "tests/test_warehouse_bigquery_job_insert_http.py",
    "tests/test_warehouse_restricted_connection_factory.py",
)


def main():
    if sys.argv[1:] not in ([], ["--c8"]):
        raise RuntimeError("ONLY_C8_FOCUSED_OPTION_ALLOWED")
    focused = sys.argv[1:] == ["--c8"]
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        for path in (ROOT / ".env", Path("D:/Project/ai-data-workspace/.env")):
            if path.is_file():
                dsn = dotenv_values(path).get("DATABASE_URL")
                if dsn:
                    break
    if not dsn:
        raise RuntimeError("LOCAL_TEST_DATABASE_URL_REQUIRED")
    settings = conninfo_to_dict(dsn)
    if settings.get("dbname") != "ai_data_workspace":
        raise RuntimeError("LOCAL_TEST_DATABASE_NAME_MISMATCH")
    if settings.get("host", "localhost") not in ("localhost", "127.0.0.1"):
        raise RuntimeError("LOOPBACK_POSTGRES_REQUIRED")
    os.environ["DATABASE_URL"] = dsn
    os.environ["APP_ENV"] = "development"
    os.environ["AUTH_MODE"] = "session"
    os.environ["AUTH_SECRET"] = "synthetic-dispatch-session-test-secret-414d5"
    os.environ["DATARISE_PUBLIC_LAUNCH_MODE"] = "off"
    os.environ["RUN_BIGQUERY_INTEGRATION"] = "0"
    sys.argv = [
        str(ROOT / "tools/task20_local_tests.py"),
        *((
            "tests/test_warehouse_named_results_c2.py",
            "tests/test_warehouse_bigquery_named_table_reader.py",
            "tests/test_warehouse_restricted_connection_factory.py",
        ) if focused else TESTS),
        "-q", "--tb=short", "-ra",
        "-p", "no:cacheprovider",
    ]
    print("TESTING: authenticated warehouse API, C2 named results,")
    print("         restricted connection checks and bounded HTTP transport")
    print("SCOPE: disposable PostgreSQL schemas, offline cloud provider")
    # Execute same validated offline-network test harness; its fixtures own
    # schema setup and teardown, never applying migration 0057 to public.
    runpy.run_path(str(ROOT / "tools/task20_local_tests.py"), run_name="__main__")


if __name__ == "__main__":
    main()
