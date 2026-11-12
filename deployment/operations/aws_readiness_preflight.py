"""Offline AWS/Terraform release preflight (NO AWS calls, NO deployment).

This is a lightweight fail-closed source/config gate, not an HCL evaluator,
IAM review, terraform plan, cost estimate, or authorization for apply.
"""
from dataclasses import dataclass
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "deployment" / "terraform" / "environments" / "dev"
TARGET_REGION = "ap-southeast-2"
EXPECTED_PROFILE = "datarise"


@dataclass(frozen=True)
class AWSPreflight:
    verdict: str
    blockers: tuple[str, ...]
    region: str
    terraform_dir: str

    @property
    def apply_authorized(self) -> bool:
        return False


def _var_default(contents: str, name: str):
    m = re.search(
        r'variable\s+"'+re.escape(name)+r'"\s*\{(.*?)(?=\n\})',
        contents, flags=re.S,
    )
    if not m:
        return None
    d = re.search(r"(?m)^\s*default\s*=\s*(\S+)", m.group(1))
    return d.group(1).strip('"') if d else None


def review_aws_source(dev: Path = DEV) -> AWSPreflight:
    blockers = set()
    required = ("versions.tf", "variables.tf", "terraform.tfvars.example",
                "rds.tf", "network_security.tf", "processing_queue.tf",
                "lambda_worker.tf", "ec2_worker.tf", "artifact_bucket.tf")
    contents = {}
    for name in required:
        path = dev / name
        if not path.is_file():
            blockers.add("MISSING_TERRAFORM_SOURCE")
            continue
        contents[name] = path.read_text(encoding="utf-8-sig")
    if len(contents) != len(required):
        return AWSPreflight("NO_GO", tuple(sorted(blockers)), TARGET_REGION, str(dev))

    variables = contents["variables.tf"]
    example = contents["terraform.tfvars.example"]
    provider = contents["versions.tf"]
    if _var_default(variables, "aws_region") != TARGET_REGION:
        blockers.add("AWS_REGION_DEFAULT_MISMATCH")
    if _var_default(variables, "worker_aws_cli_region") != TARGET_REGION:
        blockers.add("WORKER_REGION_DEFAULT_MISMATCH")
    if not re.search(r'(?m)^\s*aws_region\s*=\s*"'+TARGET_REGION+r'"', example):
        blockers.add("EXAMPLE_REGION_MISMATCH")
    # Provider must restrict known AWS account and selected credentials profile;
    # these values still require independent STS identity confirmation.
    if not re.search(r'allowed_account_ids\s*=\s*\[\s*"\d{12}"\s*\]',provider):
        blockers.add("AWS_ACCOUNT_PIN_MISSING")
    if not re.search(r'profile\s*=\s*"'+EXPECTED_PROFILE+r'"', provider):
        blockers.add("AWS_PROFILE_PIN_MISSING")
    if not re.search(r'backend\s+"local"',provider):
        blockers.add("TERRAFORM_STATE_BACKEND_REVIEW_REQUIRED")
    # Source should start with all create switches off, including network,
    # RDS and workers; these are default values, not proof about tfvars/env.
    for name in re.findall(r'variable\s+"(enable_[a-z0-9_]+)"', variables):
        if _var_default(variables, name) != "false":
            blockers.add("INFRASTRUCTURE_DEFAULT_ENABLED")
    if not re.search(r"publicly_accessible\s*=\s*false", contents["rds.tf"]):
        blockers.add("RDS_PUBLIC_ACCESS_REVIEW_REQUIRED")
    if not re.search(r"deletion_protection\s*=\s*true", contents["rds.tf"]):
        blockers.add("RDS_DELETION_PROTECTION_MISSING")
    if not re.search(r"prevent_destroy\s*=\s*true", contents["artifact_bucket.tf"]):
        blockers.add("S3_DESTROY_PROTECTION_MISSING")
    if not re.search(r"block_public_policy\s*=\s*true", contents["artifact_bucket.tf"]):
        blockers.add("S3_PUBLIC_ACCESS_REVIEW_REQUIRED")
    # Always require verified account, planned resource costs, real permissions,
    # state protection and a human-reviewed plan before any apply.
    blockers.add("AWS_IDENTITY_COST_STATE_AND_PLAN_ACCEPTANCE_REQUIRED")
    return AWSPreflight("NO_GO", tuple(sorted(blockers)), TARGET_REGION, str(dev))
