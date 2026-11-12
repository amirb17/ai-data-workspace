"""Read-only configuration guards for a dedicated API-to-RDS SG rule."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_rds_access.tf").read_text(encoding="utf-8")


def test_rule_is_opt_in_sg_to_sg_only():
    assert 'variable "enable_api_rds_ingress"' in SOURCE
    assert 'default     = false' in SOURCE
    assert 'resource "aws_vpc_security_group_ingress_rule" "api_to_rds"' in SOURCE
    assert 'referenced_security_group_id = aws_security_group.api_task[0].id' in SOURCE
    assert 'security_group_id            = var.api_rds_security_group_id' in SOURCE
    assert 'from_port                    = 5432' in SOURCE
    assert 'to_port                      = 5432' in SOURCE
    assert 'cidr_ipv4' not in SOURCE
    assert 'prevent_destroy = true' in SOURCE


def test_does_not_create_database_or_other_infrastructure():
    for forbidden in ('resource "aws_db_instance"', 'resource "aws_security_group"',
                      'resource "aws_ecs_service"', 'resource "aws_vpc"'):
        assert forbidden not in SOURCE
