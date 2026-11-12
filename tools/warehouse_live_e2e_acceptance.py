"""Single fail-closed acceptance gate for a LIVE warehouse UI -> BigQuery run.

This is not a simulated E2E success report. It explicitly reports the blockers
that would make an authenticated query fail or bypass independent duties.
It never starts a BigQuery job, mutates data, reads RPC keys or requires worker
passwords. Run it after unit tests and before an operator-authorized live test.

  python -m tools.warehouse_live_e2e_acceptance

Exit 0 means all supported preconditions were verified, but still does not
replace an actual browser -> API -> BigQuery -> accounting -> results test.
Exit 3 means the live request must remain disabled.
"""
from __future__ import annotations

import json
import sys

from fastapi import HTTPException

from app.services import warehouse_query_workflow_service as workflow
from app.services import warehouse_sql_provider_policy_service as provider
from app.services import warehouse_sql_recovery_attestation_service as attestation
from app.warehouse.worker_rpc_protocol import WorkerRpcRejected
from app.warehouse.worker_rpc_server import execute_operation
from tools.warehouse_worker_operation_readiness import inspect as permissions


def runtime_blockers():
    issues = []
    try:
        runtime = workflow.get_query_runtime()
    except HTTPException as exc:
        if exc.status_code == 503:
            issues.append("API_QUERY_RUNTIME_DISABLED")
        else:
            issues.append("API_QUERY_RUNTIME_UNEXPECTED_EXCEPTION")
    except Exception:
        issues.append("API_QUERY_RUNTIME_UNAVAILABLE")
    else:
        # Having an injected runtime is not itself proof of independently
        # scoped service-account workers or real BigQuery execution.
        issues.append("API_RUNTIME_REQUIRES_INDEPENDENT_CLOUD_VERIFICATION")

    if not provider._POLICIES:
        issues.append("REVIEWED_BIGQUERY_PROVIDER_POLICIES_UNBOUND")
    if not attestation._RUNTIMES:
        issues.append("INDEPENDENT_BIGQUERY_LOOKUP_RUNTIME_UNBOUND")

    try:
        execute_operation("provider_submission", "record_send_boundary", {})
    except WorkerRpcRejected as exc:
        if str(exc) == "PROVIDER_SEND_BOUNDARY_NOT_APPROVED":
            issues.append("ATOMIC_PROVIDER_SEND_AUTHORIZATION_NOT_IMPLEMENTED")
        else:
            issues.append("PROVIDER_SEND_BOUNDARY_REJECTED_UNEXPECTEDLY")
    except Exception:
        issues.append("PROVIDER_SEND_BOUNDARY_NOT_VERIFIED")
    else:
        issues.append("PROVIDER_SEND_BOUNDARY_REQUIRES_LIVE_VERIFICATION")
    return issues


def inspect(conn):
    grants = permissions(conn)
    issues = runtime_blockers()
    workers = grants["workers"]
    for duty, value in workers.items():
        # A disabled operation has zero required grants by design; zero
        # missing grants MUST NOT be reported as a runnable worker.
        if value.get("operation_implemented") is False:
            issues.append("WORKER_OPERATION_NOT_IMPLEMENTED:" + duty)
        elif not value["permissions_ready"]:
            issues.append("WORKER_PRIVILEGES_INCOMPLETE:" + duty)
    # These are supported static/runtime preconditions, NOT a claim that
    # browser, API, cloud, DB accounting and result data round-tripped.
    return {
        "live_e2e_ready":False,
        "query_execution_enabled":False,
        "worker_permissions":{
            duty: {
                "ready": item["permissions_ready"],
                "missing_count":len(item["missing_privileges"]),
            }
            for duty,item in workers.items()
        },
        "blockers":sorted(set(issues)),
        "message":"Do not enable live query execution; full E2E not verified.",
    }


def main():
    try:
        import psycopg
        from tools.warehouse_deployment_boundary import _dsn
        with psycopg.connect(_dsn()) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            result=inspect(conn)
        print(json.dumps(result,sort_keys=True,indent=2))
        return 3
    except Exception as exc:
        # Never echo password-bearing DSNs or provider SDK exceptions.
        print("WAREHOUSE_LIVE_E2E_GATE_BLOCKED:",
              type(exc).__name__,file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
