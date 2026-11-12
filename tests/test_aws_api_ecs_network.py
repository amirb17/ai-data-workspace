"""Offline checks: API security group is opt-in and has zero inbound rules."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_ecs_network.tf").read_text(encoding="utf-8")


def test_no_public_ingress_and_no_new_vpc():
    assert 'variable "enable_api_task_security_group"' in SOURCE
    assert "default     = false" in SOURCE
    assert 'resource "aws_security_group" "api_task"' in SOURCE
    assert 'resource "aws_vpc_security_group_ingress_rule"' not in SOURCE
    assert 'resource "aws_vpc"' not in SOURCE
    assert 'resource "aws_ecs_service"' not in SOURCE
    assert 'prevent_destroy = true' in SOURCE


def test_explicit_narrow_egress():
    assert 'resource "aws_vpc_security_group_egress_rule" "api_https"' in SOURCE
    assert 'resource "aws_vpc_security_group_egress_rule" "api_database"' in SOURCE
    assert 'from_port         = 443' in SOURCE
    assert 'from_port         = 5432' in SOURCE
    assert 'cidr_ipv4         = "172.31.0.0/16"' in SOURCE
