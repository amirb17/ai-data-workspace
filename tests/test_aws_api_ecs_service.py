"""Safety and rollout invariants for the API ECS service blueprint."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_ecs_service.tf").read_text(encoding="utf-8")


def test_service_is_opt_in_and_starts_at_zero():
    assert 'variable "enable_api_ecs_service"' in SOURCE
    assert 'variable "api_ecs_desired_count"' in SOURCE
    assert 'default     = false' in SOURCE
    assert 'default     = 0' in SOURCE
    assert 'desired_count   = var.api_ecs_desired_count' in SOURCE
    assert 'contains([0, 1], var.api_ecs_desired_count)' in SOURCE


def test_service_is_fargate_ip_targets_with_narrow_security():
    for invariant in (
        'launch_type     = "FARGATE"',
        'task_definition = aws_ecs_task_definition.api[0].arn',
        'security_groups  = [aws_security_group.api_task[0].id]',
        'target_group_arn = aws_lb_target_group.api[0].arn',
        'container_name   = "api"',
        'container_port   = 8000',
        'assign_public_ip = true',
        'enable   = true',
        'rollback = true',
        'prevent_destroy = true',
    ):
        assert invariant in SOURCE


def test_service_does_not_change_vpc_or_security_group_rules():
    assert 'resource "aws_vpc_security_group_ingress_rule"' not in SOURCE
    assert 'resource "aws_nat_gateway"' not in SOURCE
    assert 'cidr_ipv4' not in SOURCE
