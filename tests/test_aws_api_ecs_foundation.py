"""Offline safety checks for the API-only ECS foundation."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_ecs_foundation.tf").read_text(encoding="utf-8")


def test_foundation_opt_in_and_no_compute():
    assert 'variable "enable_api_ecs_foundation"' in SOURCE
    assert "default     = false" in SOURCE
    assert 'resource "aws_ecs_cluster" "api"' in SOURCE
    assert 'resource "aws_cloudwatch_log_group" "api"' in SOURCE
    for forbidden in ("aws_ecs_service", "aws_ecs_task_definition", "aws_lb",
                      "aws_nat_gateway", "aws_vpc_endpoint", "aws_db_instance"):
        assert forbidden not in SOURCE


def test_log_retention_and_non_destructive():
    assert "retention_in_days = 7" in SOURCE
    assert SOURCE.count("prevent_destroy = true") == 2
    assert 'output "api_ecs_cluster_name"' in SOURCE
    assert 'output "api_ecs_log_group_name"' in SOURCE
