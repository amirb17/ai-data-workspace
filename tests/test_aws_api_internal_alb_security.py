"""Static controls for internal API ALB pre-deployment security boundary."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_internal_alb_security.tf").read_text(encoding="utf-8")


def test_opt_in_internal_alb_sg_without_public_access():
    assert 'variable "enable_api_internal_alb_security"' in SOURCE
    assert "default     = false" in SOURCE
    assert 'resource "aws_security_group" "api_internal_alb"' in SOURCE
    assert 'resource "aws_vpc_security_group_ingress_rule" "api_from_alb"' in SOURCE
    assert 'resource "aws_vpc_security_group_egress_rule" "api_alb_to_task"' in SOURCE
    assert '"0.0.0.0/0"' not in SOURCE
    assert 'resource "aws_lb"' not in SOURCE
    assert 'resource "aws_cloudfront_distribution"' not in SOURCE
    assert 'resource "aws_ecs_service"' not in SOURCE


def test_port_8000_is_sg_to_sg_only():
    assert SOURCE.count('referenced_security_group_id = aws_security_group.api_task[0].id') == 1
    assert SOURCE.count('referenced_security_group_id = aws_security_group.api_internal_alb[0].id') == 1
    assert SOURCE.count('from_port                    = 8000') == 2
    assert 'prevent_destroy = true' in SOURCE
