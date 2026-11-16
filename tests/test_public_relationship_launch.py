"""Public relationship discovery/review remains fail-closed until explicitly opted in."""
from importlib import import_module
from types import SimpleNamespace

import pytest

from app.security.public_launch import (
    LaunchSettings,
    RELATIONSHIP_WRITE_ROUTES,
    W,
    permitted,
)


@pytest.mark.parametrize("method,path,target", [
    (method, path, target)
    for (method, path), target in RELATIONSHIP_WRITE_ROUTES.items()
])
def test_relationship_writes_require_exact_opt_in_and_endpoint(method, path, target):
    module, function = target.rsplit(".", 1)
    handler = getattr(import_module(module), function)
    route = SimpleNamespace(path=path, endpoint=handler)
    disabled = LaunchSettings(enabled=True, model_read_enabled=True)
    enabled = LaunchSettings(
        enabled=True, processing_enabled=True,
        model_read_enabled=True, relationship_write_enabled=True,
    )
    assert permitted(route, method, None) is False
    assert permitted(route, method, disabled) is False
    assert permitted(route, method, enabled) is True
    # Matching only a path/method must not authorize the wrong callable.
    assert permitted(SimpleNamespace(path=path, endpoint=lambda: None), method, enabled) is False
    assert permitted(route, "GET", enabled) is False


@pytest.mark.parametrize("method,path", [
    ("POST", W + "/relationships/readiness"),
    ("POST", W + "/relationships"),
    ("DELETE", W + "/relationships/candidates/{candidate_id}/confirm"),
    ("POST", W + "/relationships/candidates/{candidate_id}/approve"),
    ("POST", W + "/warehouse/queries"),
])
def test_relationship_opt_in_never_permits_other_routes(method, path):
    enabled = LaunchSettings(enabled=True, relationship_write_enabled=True)
    assert permitted(SimpleNamespace(path=path, endpoint=lambda: None), method, enabled) is False
