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
    assert '{ name = "DATABASE_URL", valueFrom = var.api_database_url_secret_arn }' in SOURCE
    assert 'actions   = ["secretsmanager:GetSecretValue"]' in SOURCE
    assert 'resources = [var.api_auth_secret_arn, var.api_database_url_secret_arn]' in SOURCE
    assert '"AUTH_FRONTEND_URL"' in SOURCE
    assert '"DATARISE_PUBLIC_HOSTS"' in SOURCE
    assert 'var.api_backend_origin == "https://${var.api_public_host}"' in SOURCE


def test_no_task_service_or_public_ingress():
    for forbidden in ('resource "aws_ecs_service"', 'resource "aws_lb"',
                      'resource "aws_security_group"', "assign_public_ip"):
        assert forbidden not in SOURCE
    assert "prevent_destroy = true" in SOURCE
