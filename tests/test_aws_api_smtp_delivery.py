"""Offline guardrails for the opt-in Brevo SMTP ECS deployment."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMTP = (ROOT / "deployment/terraform/environments/dev/api_smtp_delivery.tf").read_text(encoding="utf-8")
TASK = (ROOT / "deployment/terraform/environments/dev/api_ecs_task.tf").read_text(encoding="utf-8")


def test_smtp_egress_is_opt_in_starttls_only():
    assert 'variable "enable_api_smtp_delivery"' in SMTP
    assert 'default     = false' in SMTP
    assert 'resource "aws_vpc_security_group_egress_rule" "api_smtp_starttls"' in SMTP
    assert 'security_group_id = aws_security_group.api_task[0].id' in SMTP
    assert 'from_port         = 587' in SMTP
    assert 'to_port           = 587' in SMTP
    assert 'ip_protocol       = "tcp"' in SMTP
    assert 'var.api_smtp_host == "smtp-relay.brevo.com"' in SMTP


def test_smtp_iam_permission_is_limited_to_selected_secret():
    assert 'var.enable_api_smtp_delivery ? [var.api_smtp_secret_arn] : []' in TASK
    assert 'actions   = ["secretsmanager:GetSecretValue"]' in TASK
    assert '{ name = "SMTP_PASSWORD", valueFrom = "${var.api_smtp_secret_arn}:password::" }' in TASK
    assert 'SMTP_PASSWORD", value = ' not in TASK
    assert 'SMTP_USERNAME", value = ' not in TASK
