"""Offline tests for read-only AWS warehouse release gate."""
import json

from tools import warehouse_aws_release_inventory as gate


def aws(args):
    if args[:2] == ["sts", "get-caller-identity"]:
        return {"Account": gate.ACCOUNT}
    if args[:2] == ["ecs", "describe-services"]:
        return {"services": [{"status": "ACTIVE", "runningCount": 1,
                 "desiredCount": 1, "taskDefinition": "arn:aws:ecs:example"}]}
    if args[:2] == ["ecs", "describe-task-definition"]:
        return {"taskDefinition": {"containerDefinitions": [
            {"name": "api", "environment": [{"name": "WAREHOUSE_ENABLED"}],
             "secrets": []}
        ]}}
    raise AssertionError(args)


def test_production_inventory_remains_no_go_with_only_warehouse_toggle():
    report = gate.inspect(aws=aws)
    assert report["verdict"] == "NO_GO"
    assert report["query_execution_enabled"] is False
    assert report["evidence"]["account_verified"] is True
    assert report["evidence"]["ecs_api_active"] is True
    assert "RESTRICTED_QUERY_CONTROL_DSN_UNBOUND" in report["blockers"]
    assert "PROVIDER_SEND_ATOMIC_AUTHORIZATION_NOT_APPROVED" in report["blockers"]


def test_setting_control_dsn_name_alone_never_authorizes_execution():
    def configured(args):
        result = aws(args)
        if args[:2] == ["ecs", "describe-task-definition"]:
            result["taskDefinition"]["containerDefinitions"][0]["secrets"].append(
                {"name": gate.REQUIRED_API_CONFIG[0]})
        return result
    report = gate.inspect(aws=configured)
    assert report["evidence"]["restricted_control_config_present"] is True
    assert "RESTRICTED_QUERY_CONTROL_DSN_UNBOUND" not in report["blockers"]
    assert "LIVE_CONTROL_ROLE_ACCEPTANCE_UNVERIFIED" in report["blockers"]
    assert report["verdict"] == "NO_GO"


def test_foreign_account_returns_immediate_no_go_without_ecs_access():
    called = []
    def foreign(args):
        called.append(args)
        return {"Account": "000000000000"}
    report = gate.inspect(aws=foreign)
    assert called == [["sts", "get-caller-identity"]]
    assert report["blockers"] == ["WRONG_AWS_ACCOUNT"]
    assert report["evidence"]["account_verified"] is False


def test_missing_source_does_not_claim_runtime_is_ready(tmp_path):
    report = gate.inspect(aws=aws, root=tmp_path)
    assert "WAREHOUSE_SOURCE_CONTRACT_REVIEW_REQUIRED" in report["blockers"]
    assert report["live_bigquery_release_authorized"] is False


def test_all_required_config_and_extra_container_requires_cloud_iam_review():
    def multi(args):
        result = aws(args)
        if args[:2] == ["ecs", "describe-task-definition"]:
            result["taskDefinition"]["containerDefinitions"][0]["environment"].append(
                {"name": "DATARISE_WAREHOUSE_CONTROL_DSN"})
            result["taskDefinition"]["containerDefinitions"].append(
                {"name": "worker", "environment": []})
        return result
    report = gate.inspect(aws=multi)
    assert "MULTICONTAINER_TASK_IAM_ISOLATION_REVIEW_REQUIRED" in report["blockers"]
    assert "WORKER_PROCESS_AND_CLOUD_IDENTITY_PLACEMENT_UNVERIFIED" in report["blockers"]


def test_machine_readable_output_schema_has_no_secret_or_credential_material():
    report = gate.inspect(aws=aws)
    serialized = json.dumps(report)
    assert "password" not in serialized.lower()
    assert "secretvalue" not in serialized.lower()
    assert report["evidence"]["independent_worker_duties_verified"] == []
