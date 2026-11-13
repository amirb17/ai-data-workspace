"""Offline Fargate task definition guardrails; never provisions or starts compute."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_ecs_task.tf").read_text(encoding="utf-8")


def test_task_registration_is_opt_in_and_digest_pinned():
    assert 'variable "enable_api_task_definition"' in SOURCE
    assert "default     = false" in SOURCE
    assert 'resource "aws_ecs_task_definition" "api"' in SOURCE
    assert '"FARGATE"' in SOURCE
    assert 'network_mode             = "awsvpc"' in SOURCE
    assert "api_image_digest" in SOURCE
    assert '"${aws_ecr_repository.api[0].repository_url}@${var.api_image_digest}"' in SOURCE
    assert 'readonlyRootFilesystem = true' in SOURCE


def test_only_secrets_manager_not_plaintext_creds():
    assert '{ name = "AUTH_SECRET", valueFrom = var.api_auth_secret_arn }' in SOURCE
    for field, key in [
        ("DATARISE_DB_USERNAME", "username"),
        ("DATARISE_DB_PASSWORD", "password"),
        ("DATARISE_DB_HOST", "host"),
        ("DATARISE_DB_PORT", "port"),
        ("DATARISE_DB_NAME", "dbname"),
    ]:
        assert f'{{ name = "{field}", valueFrom = "${{var.api_database_secret_arn}}:{key}::" }}' in SOURCE
    assert 'actions   = ["secretsmanager:GetSecretValue"]' in SOURCE
    assert 'resources = concat([var.api_auth_secret_arn, var.api_database_secret_arn], var.enable_api_smtp_delivery ? [var.api_smtp_secret_arn] : [])' in SOURCE
    assert '"AUTH_FRONTEND_URL"' in SOURCE
    assert '"DATARISE_PUBLIC_HOSTS"' in SOURCE
    assert 'var.api_backend_origin == "https://${var.api_public_host}"' in SOURCE


def test_no_task_service_or_public_ingress():
    for forbidden in ('resource "aws_ecs_service"', 'resource "aws_lb"',
                      'resource "aws_security_group"', "assign_public_ip"):
        assert forbidden not in SOURCE
    assert "skip_destroy             = true" in SOURCE
    assert "prevent_destroy = true" not in SOURCE


def test_cloudfront_vpc_scheme_trust_requires_explicit_optin():
    assert 'variable "api_trust_cloudfront_vpc_origin"' in SOURCE
    assert 'description = "Explicitly enable HTTPS viewer scheme only behind CloudFront VPC origin and private ALB SG isolation"' in SOURCE
    assert '{ name = "DATARISE_TRUST_CLOUDFRONT_VPC_ORIGIN", value = tostring(var.api_trust_cloudfront_vpc_origin) }' in SOURCE


def test_smtp_credentials_are_opt_in_and_never_plaintext():
    assert 'var.enable_api_smtp_delivery ? [' in SOURCE
    assert '{ name = "SMTP_USERNAME", valueFrom = "${var.api_smtp_secret_arn}:username::" }' in SOURCE
    assert '{ name = "SMTP_PASSWORD", valueFrom = "${var.api_smtp_secret_arn}:password::" }' in SOURCE
    assert '{ name = "SMTP_HOST", value = var.api_smtp_host }' in SOURCE
    assert '{ name = "SMTP_FROM", value = var.api_smtp_sender }' in SOURCE
    assert '{ name = "SMTP_SSL", value = "false" }' in SOURCE
