"""Read-only AWS warehouse deployment readiness inventory.

No provisioning, credential reads, SQL, GCP calls or query submission.
Run from an authenticated AWS CLI shell:
  python -m tools.warehouse_aws_release_inventory

This is deliberately NOT a release authorization. Only a reviewed
cross-cloud security acceptance can authorize live BigQuery submission.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ACCOUNT = "202064056386"
REGION = "ap-southeast-2"
PROFILE = "datarise"
CLUSTER = "ai-workspace-dev-api"
SERVICE = "ai-workspace-dev-api"
DUTIES = (
    "provider_submission", "recovery_attestation", "accounting",
    "result_authorization",
)
ROOT = Path(__file__).resolve().parents[1]
REQUIRED_API_CONFIG = ("DATARISE_WAREHOUSE_CONTROL_DSN",)
REQUIRED_SOURCE = (
    ("app/services/warehouse_query_workflow_service.py", "_reviewed_live_runtime: InjectedQueryRuntime | None = None"),
    ("app/services/warehouse_query_control_gateway.py", "DATARISE_WAREHOUSE_CONTROL_DSN"),
    ("app/warehouse/worker_rpc_client.py", '"127.0.0.1"'),
    ("app/warehouse/worker_rpc_server.py", "PROVIDER_SEND_BOUNDARY_NOT_APPROVED"),
)


def _aws(args, *, runner=subprocess.run):
    """No shell, response payloads or credentials in errors."""
    result = runner(
        ["aws", *args, "--profile", PROFILE, "--region", REGION,
         "--output", "json"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("AWS_INSPECTION_UNAVAILABLE")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("AWS_RESPONSE_NOT_JSON") from None


def inspect(*, aws=_aws, root=ROOT):
    blockers = set()
    evidence = {"account_verified": False,
                "ecs_api_active": False,
                "warehouse_runtime_bound": False,
                "independent_worker_duties_verified": [],
                "restricted_control_config_present": False,
                "provider_submission_approved": False,
                "gcp_provider_iam_verified": False}
    try:
        identity = aws(["sts", "get-caller-identity"])
        if identity.get("Account") != ACCOUNT:
            blockers.add("WRONG_AWS_ACCOUNT")
            return _report(blockers, evidence)
        evidence["account_verified"] = True
    except (RuntimeError, OSError, subprocess.TimeoutExpired, KeyError, TypeError):
        blockers.add("AWS_IDENTITY_UNAVAILABLE")
        return _report(blockers, evidence)

    try:
        response = aws(["ecs", "describe-services", "--cluster", CLUSTER,
                        "--services", SERVICE])
        services = response.get("services") or []
        if len(services) != 1:
            raise RuntimeError("API_SERVICE_NOT_FOUND")
        service = services[0]
        evidence["ecs_api_active"] = (
            service.get("status") == "ACTIVE"
            and service.get("runningCount") == service.get("desiredCount") == 1
        )
        if not evidence["ecs_api_active"]:
            blockers.add("API_SERVICE_NOT_STABLE")
        definition = service.get("taskDefinition")
        if not isinstance(definition, str) or not definition:
            raise RuntimeError("MISSING_TASK_DEFINITION")
        task = aws(["ecs", "describe-task-definition",
                    "--task-definition", definition]).get("taskDefinition", {})
        containers = task.get("containerDefinitions", [])
        api = [c for c in containers if c.get("name") == "api"]
        if len(api) != 1:
            raise RuntimeError("API_CONTAINER_NOT_UNIQUE")
        names = {v.get("name") for v in api[0].get("environment", [])}
        names.update(v.get("name") for v in api[0].get("secrets", []))
        evidence["restricted_control_config_present"] = all(
            name in names for name in REQUIRED_API_CONFIG
        )
        if not evidence["restricted_control_config_present"]:
            blockers.add("RESTRICTED_QUERY_CONTROL_DSN_UNBOUND")
        # Never interpret a configured DSN name as a verified, authenticated
        # datarise_wh_control login or adequate effective privileges.
        blockers.add("LIVE_CONTROL_ROLE_ACCEPTANCE_UNVERIFIED")
        # All containers in an ECS task share a task role. Never infer
        # independently scoped IAM authority from separate containers.
        if len(containers) > 1:
            blockers.add("MULTICONTAINER_TASK_IAM_ISOLATION_REVIEW_REQUIRED")
    except (RuntimeError, OSError, subprocess.TimeoutExpired, KeyError, TypeError, AttributeError):
        blockers.add("API_ECS_INSPECTION_UNAVAILABLE")

    for path, expected in REQUIRED_SOURCE:
        try:
            contents = (root / path).read_text(encoding="utf-8-sig")
            if expected not in contents:
                blockers.add("WAREHOUSE_SOURCE_CONTRACT_REVIEW_REQUIRED")
        except OSError:
            blockers.add("WAREHOUSE_SOURCE_CONTRACT_REVIEW_REQUIRED")
    # The existing loopback client cannot reach a worker in a different
    # ECS task; workers also cannot share a cloud task IAM role merely because
    # each container has its own process or database login.
    blockers.add("WORKER_PROCESS_AND_CLOUD_IDENTITY_PLACEMENT_UNVERIFIED")
    blockers.add("PROVIDER_SEND_ATOMIC_AUTHORIZATION_NOT_APPROVED")
    blockers.add("GCP_BIGQUERY_CREATOR_ATTESTER_READER_IAM_UNVERIFIED")
    blockers.add("PROVIDER_POLICY_AND_NAMED_RESULTS_UNBOUND")
    blockers.add("SERVING_PUBLICATION_AND_RETENTION_UNVERIFIED")
    blockers.add("ANALYST_WAREHOUSE_UI_E2E_UNVERIFIED")
    return _report(blockers, evidence)


def _report(blockers, evidence):
    return {
        "aws_account": ACCOUNT,
        "aws_region": REGION,
        "query_execution_enabled": False,
        "live_bigquery_release_authorized": False,
        "verdict": "NO_GO",
        "blockers": sorted(blockers),
        "evidence": evidence,
    }


def main():
    report = inspect()
    print(json.dumps(report, sort_keys=True, indent=2))
    return 2 if "WRONG_AWS_ACCOUNT" in report["blockers"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
