"""Fail-closed public workspace/dataset creation switch (no AWS side effects)."""
from pathlib import Path
import pytest
from app.security.public_launch import LaunchSettings, permitted, WORKSPACE_CREATE, POLICY
from app.main import app


def routes():
    from app.security.public_launch import http_routes
    return list(http_routes(app.router.routes))


def route(path, method):
    return next(r for r in routes() if r.path == path and method in r.methods)


def test_creation_rejected_by_default_and_allowed_only_with_switch():
    disabled = LaunchSettings(True, ("api.example.org",), True)
    enabled = LaunchSettings(True, ("api.example.org",), True, True)
    for method, path in WORKSPACE_CREATE:
        r = route(path, method)
        assert not permitted(r, method, disabled)
        assert permitted(r, method, enabled)
        assert not permitted(r, method)  # other callers cannot implicitly opt in


def test_no_other_write_route_unlocked():
    enabled = LaunchSettings(True, ("api.example.org",), True, True)
    assert set(WORKSPACE_CREATE) == {
        ("POST", "/workspaces"),
        ("POST", "/workspaces/{workspace_id}/datasets"),
    }
    for path in ("/files/uploads", "/workspaces/{workspace_id}/datasets/{dataset_id}/processing/pending"):
        r = next((r for r in routes() if r.path == path and "POST" in r.methods), None)
        if r is not None:
            assert not permitted(r, "POST", enabled)
    assert ("POST", "/workspaces") not in POLICY


def test_terraform_optin_defaults_off_and_keeps_warehouse_disabled():
    source = (Path(__file__).resolve().parents[1] /
              "deployment/terraform/environments/dev/api_ecs_task.tf").read_text()
    assert 'variable "api_enable_public_workspace_creation"' in source
    segment = source.split('variable "api_enable_public_workspace_creation"')[1].split("}", 1)[0]
    assert "default     = false" in segment
    assert '{ name = "DATARISE_PUBLIC_WORKSPACE_CREATION", value = tostring(var.api_enable_public_workspace_creation) }' in source
    assert '{ name = "WAREHOUSE_ENABLED", value = "false" }' in source


def test_launch_settings_require_exact_boolean_optin(monkeypatch):
    from app.security import public_launch
    from app.auth.config import config
    monkeypatch.setenv("DATARISE_PUBLIC_LAUNCH_MODE", "off")
    monkeypatch.setenv("DATARISE_PUBLIC_WORKSPACE_CREATION", "unexpected")
    assert not public_launch.launch_settings().enabled
