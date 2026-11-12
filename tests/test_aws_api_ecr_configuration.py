"""Offline checks of dedicated API registry. Does not create AWS resources."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ECR = ROOT / "deployment/terraform/environments/dev/api_ecr.tf"


def test_api_registry_is_opt_in_and_separate():
    source = ECR.read_text(encoding="utf-8")
    assert 'variable "enable_api_ecr"' in source
    assert "default     = false" in source
    assert 'resource "aws_ecr_repository" "api"' in source
    assert 'name                 = "ai-workspace-${var.environment}-api"' in source
    assert "image_tag_mutability = \"IMMUTABLE\"" in source
    assert "force_delete         = false" in source
    assert "scan_on_push = true" in source
    assert "prevent_destroy = true" in source
    assert 'output "api_ecr_repository_url"' in source


def test_api_registry_does_not_create_workers():
    source = ECR.read_text(encoding="utf-8")
    assert "aws_ecs_service" not in source
    assert "aws_iam_role" not in source
    assert "aws_ec2_instance" not in source
