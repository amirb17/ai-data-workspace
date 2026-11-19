"""Fail-closed static guard for the opt-in private warehouse ECS module.

These tests are not terraform validate or production IAM acceptance.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "deployment/terraform/modules/warehouse_workers"


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_four_worker_duties_are_explicit_and_independent():
    main = read("main.tf")
    variables = read("variables.tf")
    assert re.search(r'provider_submission\s*=\s*18441', main)
    assert re.search(r'recovery_attestation\s*=\s*18442', main)
    assert re.search(r'accounting\s*=\s*18443', main)
    assert re.search(r'result_authorization\s*=\s*18444', main)
    assert 'length(setsubtract(' in variables
    assert 'length(var.duties) == 4' in variables
    assert 'length(distinct([for _, d in var.duties : d.db_dsn_secret_arn])) == 4' in variables
    assert 'length(distinct([for _, d in var.duties : d.rpc_secret_arn])) == 4' in variables
    assert 'length(distinct([for _, d in var.duties : d.efs_access_point_arn])) == 4' in variables


def test_workers_have_separate_execution_and_task_roles():
    main = read("main.tf")
    assert 'resource "aws_iam_role" "execution"' in main
    assert 'resource "aws_iam_role" "task"' in main
    assert 'task_role_arn            = aws_iam_role.task[each.key].arn' in main
    assert 'execution_role_arn       = aws_iam_role.execution[each.key].arn' in main
    assert 'for_each = local.duties' in main
    assert 'secretsmanager:GetSecretValue' in main
    assert 'var.duties[each.key].db_dsn_secret_arn' in main
    assert 'var.duties[each.key].rpc_secret_arn' in main
    assert 'Action = ["elasticfilesystem:ClientMount"]' in main
    assert 'elasticfilesystem:AccessPointArn' in main


def test_private_network_only_and_no_public_task_ip():
    main = read("main.tf")
    assert 'assign_public_ip = false' in main
    assert 'referenced_security_group_id = var.api_security_group_id' in main
    assert 'referenced_security_group_id = var.database_security_group_id' in main
    assert 'referenced_security_group_id = var.efs_security_group_id' in main
    assert 'referenced_security_group_id = var.vpc_endpoint_security_group_id' in main
    assert '"warehouse.internal"' in main
    assert '0.0.0.0/0' not in main


def test_exact_image_and_mutual_tls_server_required():
    main = read("main.tf")
    variables = read("variables.tf")
    assert 'image     = var.image_digest_uri' in main
    assert '--private-mtls' in main
    assert 'transit_encryption = "ENABLED"' in main
    assert 'readOnly      = true' in main
    assert 'readonlyRootFilesystem = true' in main
    assert 'regex("@sha256:[0-9a-f]{64}$"' in variables
    assert 'aws_ecs_service" "worker"' in main
    assert 'desired_count   = 1' in main


def test_module_does_not_mint_provider_privileges_or_enable_live_dispatch():
    main = read("main.tf")
    for forbidden in (
        'bigquery.jobs.create', 'bigquery.tables.getData',
        'DATARISE_WAREHOUSE_CONTROL_DSN', 'QUERY_EXECUTION_ENABLED',
        'WAREHOUSE_QUERY_EXECUTION_ENABLED',
        'secretsmanager:*', 's3:*', 'iam:PassRole',
        'aws_iam_policy_attachment',
    ):
        assert forbidden not in main


def test_existing_secrets_endpoint_reachable_without_broad_egress():
    main = read("main.tf")
    variables = read("variables.tf")
    outputs = read("outputs.tf")
    assert 'resource "aws_vpc_security_group_egress_rule" "worker_to_secrets_manager"' in main
    assert 'referenced_security_group_id = var.secrets_manager_endpoint_security_group_id' in main
    assert 'var.secrets_manager_endpoint_security_group_id == "sg-0756562b88e103959"' in variables
    assert 'output "worker_security_group_id"' in outputs
    assert 'output "private_worker_dns_names"' in outputs
    assert '0.0.0.0/0' not in main
