"""AWS offline release gate. Never invokes AWS, Docker or Terraform."""
from dataclasses import FrozenInstanceError
from pathlib import Path
from shutil import copyfile

import pytest

from deployment.operations.aws_readiness_preflight import review_aws_source, DEV


def test_repo_configuration_is_reviewable_but_apply_remains_denied():
    result = review_aws_source()
    assert result.verdict == "NO_GO"
    assert not result.apply_authorized
    assert "AWS_REGION_DEFAULT_MISMATCH" not in result.blockers
    assert "WORKER_REGION_DEFAULT_MISMATCH" not in result.blockers
    assert "AWS_ACCOUNT_PIN_MISSING" not in result.blockers
    assert "INFRASTRUCTURE_DEFAULT_ENABLED" not in result.blockers
    assert "AWS_IDENTITY_COST_STATE_AND_PLAN_ACCEPTANCE_REQUIRED" in result.blockers


def test_missing_files_fail_closed(tmp_path):
    result = review_aws_source(tmp_path)
    assert "MISSING_TERRAFORM_SOURCE" in result.blockers
    assert result.verdict == "NO_GO"


def test_unsafe_region_and_enabled_resources_fail_closed(tmp_path):
    for filename in ("versions.tf", "variables.tf", "terraform.tfvars.example",
                     "rds.tf", "network_security.tf", "processing_queue.tf",
                     "lambda_worker.tf", "ec2_worker.tf", "artifact_bucket.tf"):
        copyfile(DEV / filename, tmp_path / filename)
    p = tmp_path / "variables.tf"
    content = p.read_text(encoding="utf-8")
    content = content.replace('default     = "ap-south-1"', 'default     = "eu-west-1"', 1)
    content = content.replace('default     = false', 'default     = true', 1)
    p.write_text(content, encoding="utf-8")
    result = review_aws_source(tmp_path)
    assert "AWS_REGION_DEFAULT_MISMATCH" in result.blockers
    assert "INFRASTRUCTURE_DEFAULT_ENABLED" in result.blockers


def test_missing_account_pin_is_blocked(tmp_path):
    for filename in ("versions.tf", "variables.tf", "terraform.tfvars.example",
                     "rds.tf", "network_security.tf", "processing_queue.tf",
                     "lambda_worker.tf", "ec2_worker.tf", "artifact_bucket.tf"):
        copyfile(DEV / filename, tmp_path / filename)
    p = tmp_path / "versions.tf"
    p.write_text(p.read_text().replace("allowed_account_ids", "not_the_account_pin"))
    assert "AWS_ACCOUNT_PIN_MISSING" in review_aws_source(tmp_path).blockers


def test_no_shell_or_network_side_effects():
    import ast
    p = Path(__file__).resolve().parents[1] / "deployment/operations/aws_readiness_preflight.py"
    tree = ast.parse(p.read_text(encoding="utf-8"))
    modules = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
               for a in n.names]
    assert not any(m.startswith(("boto3", "requests", "subprocess", "socket")) for m in modules)
