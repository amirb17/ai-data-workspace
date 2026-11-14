"""Fail-closed route and SQL authority assertions for first APPEND activation."""
from pathlib import Path
from types import SimpleNamespace

from app.security.public_launch import LaunchSettings, permitted, INITIAL_APPEND_ROUTES


def test_initial_append_not_public_without_explicit_opt_in():
    from app.api.incremental import initial_append_policy
    path = next(iter(INITIAL_APPEND_ROUTES))[1]
    route = SimpleNamespace(path=path, endpoint=initial_append_policy)
    assert not permitted(route, "POST", LaunchSettings())
    assert not permitted(route, "POST", LaunchSettings(
        enabled=True, processing_enabled=True, rule_review_enabled=True))
    assert permitted(route, "POST", LaunchSettings(
        enabled=True, processing_enabled=True, rule_review_enabled=True,
        initial_append_enabled=True))
    assert not permitted(route, "GET", LaunchSettings(
        enabled=True, processing_enabled=True, rule_review_enabled=True,
        initial_append_enabled=True))


def test_policy_function_narrow_grant_and_guards():
    sql = (Path(__file__).resolve().parents[1] /
           "migrations/0062_initial_append_from_verified_silver.sql").read_text()
    assert "SECURITY DEFINER" in sql
    assert "task20_source(actor, upload)" in sql
    assert "current_state_id IS NOT NULL" not in sql
    assert "state_id IS NOT NULL" in sql
    assert "c.status='VERIFIED' AND c.verification_report IS NOT NULL" in sql
    assert "dq.silver_path IS NOT NULL AND pa.status='SUCCESS'" in sql
    assert "GRANT EXECUTE ON FUNCTION task20_initial_append_policy(BIGINT,BIGINT) TO datarise_api" in sql
    assert "GRANT INSERT ON dataset_load_policies TO datarise_api" not in sql
    assert "GRANT ALL" not in sql


def test_release_defaults_disabled():
    terraform = (Path(__file__).resolve().parents[1] /
                 "deployment/terraform/environments/dev/api_ecs_task.tf").read_text()
    assert 'variable "api_enable_initial_append"' in terraform
    assert 'DATARISE_PUBLIC_INITIAL_APPEND' in terraform
    assert 'default     = false' in terraform.split('variable "api_enable_initial_append"')[1].split("}",1)[0]
