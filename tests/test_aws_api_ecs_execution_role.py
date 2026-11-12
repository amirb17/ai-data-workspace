"""Offline guardrails for dedicated FastAPI ECS execution identity."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_ecs_execution_role.tf").read_text(encoding="utf-8")


def test_opt_in_and_task_only_trust():
    assert 'variable "enable_api_ecs_execution_role"' in SOURCE
    assert "default     = false" in SOURCE
    assert '"ecs-tasks.amazonaws.com"' in SOURCE
    assert '"sts:AssumeRole"' in SOURCE
    assert 'resource "aws_iam_role" "api_ecs_execution"' in SOURCE
    assert "prevent_destroy = true" in SOURCE


def test_agent_permissions_not_app_db_or_worker_permissions():
    assert 'arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy' in SOURCE
    assert "aws_iam_role_policy_attachment" in SOURCE
    assert "secretsmanager:GetSecretValue" not in SOURCE
    assert "rds-db:connect" not in SOURCE
    assert "s3:*" not in SOURCE
