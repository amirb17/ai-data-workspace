"""Authenticated, independent read-only Model/Relationships gate."""
from types import SimpleNamespace
from importlib import import_module
from pathlib import Path
from app.security.public_launch import LaunchSettings, MODEL_READ_ROUTES, permitted


def route(path, target):
    module, name = target.rsplit(".", 1)
    return SimpleNamespace(path=path, endpoint=getattr(import_module(module), name))


def test_model_routes_require_explicit_opt_in():
    allowed = LaunchSettings(enabled=True, analytics_read_enabled=True, model_read_enabled=True)
    blocked = LaunchSettings(enabled=True, analytics_read_enabled=True)
    for (method, path), target in MODEL_READ_ROUTES.items():
        actual = route(path, target)
        assert not permitted(actual, method, blocked)
        assert permitted(actual, method, allowed)
        assert not permitted(actual, "POST", allowed)
        assert not permitted(SimpleNamespace(path=path, endpoint=lambda: None), method, allowed)


def test_model_release_leaves_unapproved_semantic_and_ai_operations_disabled():
    approved = LaunchSettings(enabled=True, analytics_read_enabled=True, model_read_enabled=True)
    for method, path in (
        ("GET", "/workspaces/{workspace_id}/datasets/{dataset_id}/semantic-understanding/readiness"),
        ("POST", "/workspaces/{workspace_id}/relationships/discover"),
        ("POST", "/workspaces/{workspace_id}/questions"),
        ("GET", "/workspaces/{workspace_id}/questions/answers"),
    ):
        assert not permitted(SimpleNamespace(path=path, endpoint=lambda:None),method,approved)


def test_model_terraform_remains_default_off():
    source=(Path(__file__).resolve().parents[1]/"deployment/terraform/environments/dev/api_ecs_task.tf").read_text()
    assert 'variable "api_enable_model_read"' in source
    assert 'default     = false' in source
    assert "DATARISE_PUBLIC_MODEL_READ" in source
