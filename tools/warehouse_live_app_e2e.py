"""Real authenticated Warehouse SQL HTTP acceptance, never an offline/mock test.

Runs validate -> admit -> duplicate admit -> execute -> refresh/status -> result,
using an existing logged-in user, a real published warehouse model, the running
API and its configured real cloud/runtime identities. Makes NO database changes
outside normal API operations. May run a billable BigQuery SELECT; uses the
existing enforced budget, and never bypasses the production 503 gate.

Example:
 python tools/warehouse_live_app_e2e.py --workspace 18 --model 42 \
    --sql "SELECT line FROM model_42 LIMIT 1"

The script prompts locally for the CURRENT browser's session and CSRF cookie
values (NEVER paste these into chat). Authentication is sent only to an explicit
loopback backend, never to an arbitrary remote endpoint. The script redacts all
HTTP details, tokens and SQL in diagnostics.
"""
from __future__ import annotations

import argparse
import getpass
import json
import re
import secrets
import sys
import time
from urllib.parse import urlsplit

import requests

_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"})
_HANDLE = re.compile(r"[a-f0-9]{32}\Z")
_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


class AcceptanceError(RuntimeError):
    pass


def _post(session, url, payload=None):
    try:
        response = session.post(url, json=payload, timeout=(3, 25), allow_redirects=False)
    except requests.RequestException:
        raise AcceptanceError("LOCAL_HTTP_CONNECTION_FAILED") from None
    return _result(response)


def _get(session, url):
    try:
        response = session.get(url, timeout=(3, 25), allow_redirects=False)
    except requests.RequestException:
        raise AcceptanceError("LOCAL_HTTP_CONNECTION_FAILED") from None
    return _result(response)


def _result(response):
    if not 200 <= response.status_code < 300:
        # No server exception strings, cookies or SQL echoed.
        raise AcceptanceError(f"WAREHOUSE_API_STATUS_{response.status_code}")
    if not response.headers.get("content-type", "").lower().startswith("application/json"):
        raise AcceptanceError("WAREHOUSE_API_NON_JSON")
    try:
        value = response.json()
    except (ValueError, json.JSONDecodeError):
        raise AcceptanceError("WAREHOUSE_API_MALFORMED_JSON") from None
    if type(value) is not dict:
        raise AcceptanceError("WAREHOUSE_API_INVALID_JSON_SHAPE")
    return value


def run(args, *, session=None, cookie=None, csrf=None, wait=time.sleep):
    resume = getattr(args, "resume_handle", None)
    if resume is not None and (type(resume) is not str or not _HANDLE.fullmatch(resume)):
        raise AcceptanceError("ORIGINAL_ATTEMPT_HANDLE_REQUIRED")
    if not 1 <= args.workspace <= 2**63-1:
        raise AcceptanceError("INVALID_WAREHOUSE_SCOPE")
    if resume is None:
        if not isinstance(args.sql, str) or not re.fullmatch(
                r"SELECT\\s+[\\s\\S]{1,16000}", args.sql.strip(), re.I):
            raise AcceptanceError("SELECT_ONLY_DEMO_REQUIRED")
        if ";" in args.sql or "--" in args.sql or "/*" in args.sql:
            raise AcceptanceError("ONE_SINGLE_SELECT_REQUIRED")
        if args.model is None or not 1 <= args.model <= 2**63-1:
            raise AcceptanceError("INVALID_WAREHOUSE_SCOPE")
    base = urlsplit(args.backend)
    origin = urlsplit(args.origin)
    if (base.scheme != "http" or base.hostname not in ("127.0.0.1", "localhost")
            or base.port is None or base.username or base.password
            or base.path not in ("", "/") or base.query or base.fragment
            or origin.scheme != "http"
            or origin.hostname not in ("127.0.0.1", "localhost")
            or origin.port is None or origin.username or origin.password
            or origin.path not in ("", "/") or origin.query or origin.fragment):
        raise AcceptanceError("EXPLICIT_LOOPBACK_HTTP_ORIGIN_REQUIRED")
    if session is None:
        session = requests.Session()
        session.trust_env = False
        session.max_redirects = 0
    if cookie is None:
        cookie = getpass.getpass("Local session cookie (hidden): ")
    if csrf is None:
        csrf = getpass.getpass("Local CSRF cookie (hidden): ")
    if not cookie or not csrf or len(cookie) > 256 or len(csrf) > 256:
        raise AcceptanceError("CURRENT_SESSION_COOKIES_REQUIRED")
    session.cookies.set("datarise_session", cookie, path="/", domain=base.hostname)
    session.cookies.set("datarise_csrf", csrf, path="/", domain=base.hostname)
    session.headers.update({"Origin": args.origin.rstrip("/"), "X-CSRF-Token": csrf})
    url = f"{args.backend.rstrip('/')}/workspaces/{args.workspace}/warehouse/queries"
    print("LIVE: real API endpoint, no offline provider or fake cloud")
    if resume is None:
        # Reuse exact same request identity to test duplicate admission.
        key = "e2e_" + secrets.token_hex(24)
        assert _KEY.fullmatch(key)
        print("CHECK: authenticated SQL validation", flush=True)
        validated = _post(session, url + "/validate",
                          {"model_id": args.model, "sql": args.sql})
        if validated.get("budget_decision") != "ALLOWED":
            raise AcceptanceError("GOVERNANCE_BUDGET_NOT_ALLOWED")
        print("CHECK: admission + idempotent duplicate admission", flush=True)
        payload = {"model_id": args.model, "sql": args.sql, "idempotency_key": key}
        first = _post(session, url, payload)
        handle = first.get("attempt_handle")
        if type(handle) is not str or not _HANDLE.fullmatch(handle):
            raise AcceptanceError("MISSING_ATTEMPT_HANDLE")
        # Print before any possible send. Failures can then resume the same job.
        print("ORIGINAL ATTEMPT:", handle, flush=True)
        again = _post(session, url, payload)
        if again.get("attempt_handle") != handle or again.get("reused") is not True:
            raise AcceptanceError("DUPLICATE_ADMISSION_NOT_REUSED")
        print("CHECK: submit/reconcile verified original job", flush=True)
        attempt_url = url + "/" + handle
        state = _post(session, attempt_url + "/execute")
        if state.get("attempt_handle") != handle:
            raise AcceptanceError("ATTEMPT_IDENTITY_CHANGED")
    else:
        # No new validation, admission or submission on recovery.
        handle = resume
        print("RESUMING ORIGINAL ATTEMPT:", handle, flush=True)
        attempt_url = url + "/" + handle
        state = _get(session, attempt_url)
    deadline = time.monotonic() + args.timeout
    while state.get("status") not in _TERMINAL and time.monotonic() < deadline:
        wait(2)
        state = _get(session, attempt_url)
        if state.get("status") in ("RUNNING", "PREPARED", "UNCERTAIN"):
            # Refresh is deliberately guarded by existing lease fencing;
            # a 409 busy response isn't a provider retry permit.
            try:
                state = _post(session, attempt_url + "/refresh")
            except AcceptanceError as exc:
                if str(exc) != "WAREHOUSE_API_STATUS_409":
                    raise
    status = state.get("status")
    print("RESULT STATUS:", status, flush=True)
    if status != "SUCCEEDED":
        raise AcceptanceError("LIVE_WAREHOUSE_QUERY_NOT_SUCCESSFUL")
    result = _get(session, attempt_url + "/results")
    columns, rows = result.get("columns"), result.get("rows")
    if (type(columns) is not list or type(rows) is not list
            or not 1 <= len(columns) <= 32 or len(rows) > 100
            or any(type(row) is not list or len(row) != len(columns) for row in rows)):
        raise AcceptanceError("INVALID_LIVE_RESULT_SCHEMA")
    print("LIVE VERIFIED: authenticated admission, idempotency, provider",
          "reconciliation, settlement, authorized result page", flush=True)
    print("COLUMNS:", ", ".join(str(column) for column in columns))
    print("ROWS:", len(rows))
    for row in rows[:5]:
        print("  ", json.dumps(row, ensure_ascii=False, default=str))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--origin", default="http://localhost:5173")
    parser.add_argument("--workspace", type=int, required=True)
    parser.add_argument("--model", type=int)
    parser.add_argument("--sql")
    parser.add_argument("--resume-handle", help="Recover one original attempt without a new submission")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if not 10 <= args.timeout <= 300:
        parser.error("--timeout must be between 10 and 300 seconds")
    try:
        return run(args)
    except AcceptanceError as error:
        print("LIVE ACCEPTANCE BLOCKED:", error, file=sys.stderr)
        return 2
    except Exception:
        print("LIVE ACCEPTANCE BLOCKED: unexpected internal error",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
