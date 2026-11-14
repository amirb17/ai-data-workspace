"""Fail-closed public Analytics and SQL catalog release gates."""
from types import SimpleNamespace
from app.security.public_launch import (
    LaunchSettings, permitted, ANALYTICS_READ_ROUTES,
    ANALYTICS_REFRESH_ROUTES, SQL_CATALOG_ROUTES,
)


def route(path, target):
    from importlib import import_module
    module, name = target.rsplit(".", 1)
    return SimpleNamespace(path=path, endpoint=getattr(import_module(module), name))


def test_analytics_and_sql_routes_default_off():
    for routes in (ANALYTICS_READ_ROUTES, ANALYTICS_REFRESH_ROUTES, SQL_CATALOG_ROUTES):
        for (method, path), target in routes.items():
            r = route(path, target)
            assert not permitted(r, method, LaunchSettings())
            assert not permitted(r, method, LaunchSettings(enabled=True))


def test_explicit_analytics_flags_grant_only_exact_methods_and_endpoints():
    settings = LaunchSettings(enabled=True, analytics_read_enabled=True,
                              analytics_refresh_enabled=True, sql_catalog_enabled=True)
    for routes in (ANALYTICS_READ_ROUTES, ANALYTICS_REFRESH_ROUTES, SQL_CATALOG_ROUTES):
        for (method, path), target in routes.items():
            r = route(path, target)
            assert permitted(r, method, settings)
            assert not permitted(r, "GET" if method == "POST" else "POST", settings)
            assert not permitted(SimpleNamespace(path=path, endpoint=lambda: None), method, settings)
    # No query execution, builder or save-queries permissions in this release.
    for path in ("/workspaces/{workspace_id}/sql/execute",
                 "/workspaces/{workspace_id}/sql/validate",
                 "/workspaces/{workspace_id}/sql/builder",
                 "/workspaces/{workspace_id}/sql/queries"):
        assert not permitted(SimpleNamespace(path=path, endpoint=lambda: None), "POST", settings)


def test_partial_feature_gates_do_not_expand_routes():
    dashboard = next(iter(ANALYTICS_READ_ROUTES.items()))
    refresh = next(iter(ANALYTICS_REFRESH_ROUTES.items()))
    sql = next(iter(SQL_CATALOG_ROUTES.items()))
    read_only = LaunchSettings(enabled=True, analytics_read_enabled=True)
    assert permitted(route(dashboard[0][1], dashboard[1]), "GET", read_only)
    assert not permitted(route(refresh[0][1], refresh[1]), "POST", read_only)
    assert not permitted(route(sql[0][1], sql[1]), "GET", read_only)
