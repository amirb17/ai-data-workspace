"""HTTP-to-PostgreSQL verification for reviewed warehouse joins.

The database fixture uses a disposable PostgreSQL schema. The identity override
is confined to the TestClient; production authentication remains unchanged.
"""
import pytest
from fastapi.testclient import TestClient

from app.services import warehouse_model_service as models
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_relationship_service import context


@pytest.fixture
def client_context(modeling_pg, monkeypatch):
    pg = modeling_pg
    relation = context(pg)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_MODE", "development")
    monkeypatch.setenv("ENABLE_DEV_IDENTITY", "true")
    monkeypatch.setenv("AUTH_FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setenv("AUTH_BACKEND_URL", "http://localhost:8000")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("DATARISE_PUBLIC_LAUNCH_MODE", "off")
    from app.main import app
    from app.api.identity import get_current_user
    identity = {"user": pg["owner"]}
    app.dependency_overrides[get_current_user] = lambda: identity["user"]
    try:
        with TestClient(app) as client:
            yield client, pg, relation, identity
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_http_to_database_to_catalog_and_reviewed_join(client_context):
    client, pg, relation, identity = client_context
    workspace_id = pg["workspace"]
    base = f"/workspaces/{workspace_id}/warehouse"
    before = client.get(f"{base}/catalog")
    assert before.status_code == 200, before.text
    catalog = before.json()
    assert catalog["workspace_id"] == workspace_id
    models_by_id = {m["model_id"]: m for m in catalog["models"]}
    assert models_by_id[relation.fact_model_id]["model_type"] == "FACT"
    assert models_by_id[relation.dimension_model_id]["model_type"] == "DIMENSION"
    assert all(m["serving_status"] == "NOT_VERIFIED" and not m["queryable"]
               for m in catalog["models"])
    assert not catalog["query_execution_enabled"]

    empty = client.get(f"{base}/relationships")
    assert empty.status_code == 200, empty.text
    assert empty.json()["relationships"] == []

    created = client.post(f"{base}/relationships", json=relation.model_dump(mode="json"))
    assert created.status_code == 201, created.text
    relation_id = created.json()["relationship_id"]
    assert relation_id > 0

    read = client.get(f"{base}/relationships")
    assert read.status_code == 200, read.text
    rows = read.json()["relationships"]
    assert len(rows) == 1
    assert rows[0]["relationship_id"] == relation_id
    assert rows[0]["fact_model_version_id"] == models_by_id[relation.fact_model_id]["model_version_id"]
    assert rows[0]["dimension_model_version_id"] == models_by_id[relation.dimension_model_id]["model_version_id"]
    assert rows[0]["temporal_policy"] == "EVENT_TIME_AS_OF"

    identity["user"] = pg["other"]
    for endpoint in ("catalog", "relationships"):
        forbidden = client.get(f"{base}/{endpoint}")
        assert forbidden.status_code == 403, forbidden.text
    assert client.post(f"{base}/relationships", json=relation.model_dump(mode="json")).status_code == 403

    identity["user"] = pg["owner"]
    models.archive_model(workspace_id, relation.fact_model_id, pg["owner"])
    assert client.get(f"{base}/relationships").json()["relationships"] == []
    remaining = client.get(f"{base}/catalog").json()["models"]
    assert relation.fact_model_id not in {m["model_id"] for m in remaining}


def test_disabled_feature_fails_closed_over_http(client_context, monkeypatch):
    client, pg, relation, identity = client_context
    monkeypatch.setenv("WAREHOUSE_ENABLED", "false")
    base = f"/workspaces/{pg['workspace']}/warehouse"
    assert client.get(f"{base}/catalog").status_code == 409
    assert client.get(f"{base}/relationships").status_code == 409
    assert client.post(f"{base}/relationships", json=relation.model_dump(mode="json")).status_code == 409


def test_public_launch_does_not_allow_warehouse_endpoints(client_context):
    from app.main import app
    from app.security.public_launch import permitted, http_routes
    endpoints = [
        route for route in http_routes(app.router.routes)
        if getattr(route, "path", "").endswith(("/warehouse/catalog", "/warehouse/relationships"))
    ]
    expected = {
        ("GET", "/workspaces/{workspace_id}/warehouse/catalog"),
        ("GET", "/workspaces/{workspace_id}/warehouse/relationships"),
        ("POST", "/workspaces/{workspace_id}/warehouse/relationships"),
    }
    actual = {
        (method, route.path)
        for route in endpoints
        for method in route.methods
    }
    assert actual == expected
    for route in endpoints:
        for method in route.methods:
            assert not permitted(route, method)
