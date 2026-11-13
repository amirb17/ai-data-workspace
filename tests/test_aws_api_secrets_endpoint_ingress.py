"""Network permission is least privilege and disabled by default."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_secrets_endpoint_ingress.tf").read_text(encoding="utf-8")


def test_secrets_endpoint_rule_only_from_api_security_group():
    assert 'variable "enable_api_secrets_endpoint_ingress"' in SOURCE
    assert 'default     = false' in SOURCE
    assert 'resource "aws_vpc_security_group_ingress_rule" "api_to_secrets_endpoint"' in SOURCE
    assert 'security_group_id = var.api_secrets_endpoint_security_group_id' in SOURCE
    assert 'referenced_security_group_id = aws_security_group.api_task[0].id' in SOURCE
    assert 'from_port         = 443' in SOURCE
    assert 'to_port           = 443' in SOURCE
    assert 'prevent_destroy = true' in SOURCE


def test_no_broad_network_changes():
    for forbidden in ('0.0.0.0/0', 'cidr_ipv4', 'resource "aws_vpc_endpoint"',
                      'resource "aws_nat_gateway"', 'resource "aws_security_group"'):
        assert forbidden not in SOURCE
