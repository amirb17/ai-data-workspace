"""Issue #1 route, capability, CSRF and disclosure regression checks (offline)."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.identity import get_current_user
from app.api import product_preview, workspace_model, workspace_questions
from app.security.public_launch import PublicLaunchBoundary, LaunchSettings, permitted, http_routes
from app.db import database


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('AUTH_MODE', 'session')
    monkeypatch.setenv('AUTH_SECRET', 'synthetic-issue1-secret-' * 3)
    app.dependency_overrides[get_current_user] = lambda: {'user_id': 7, 'owner_key': 'owner:7'}
    monkeypatch.setattr('app.api.identity.get_current_user', lambda request: {'user_id': 7, 'owner_key': 'owner:7'})
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_capabilities_public_processing_does_not_query_warehouse(client, monkeypatch):
    launch = LaunchSettings(True, ('testserver',), processing_enabled=True)
    monkeypatch.setattr('app.security.public_launch.launch_settings', lambda: launch)
    monkeypatch.setattr(product_preview.service, 'authorize_read', lambda *a: None)
    monkeypatch.setattr(product_preview.service, 'capabilities', lambda *a: pytest.fail('warehouse dependency'))
    with TestClient(PublicLaunchBoundary(app, settings=launch, routes=app.routes), base_url='https://testserver') as c:
        body = c.get('/workspaces/1/datasets/2/product/capabilities').json()
    assert body['processing_submission'] == 'ENABLED'
    assert body['warehouse_models'] == []
    assert body['warehouse'] == 'NOT_CONFIGURED'


def test_model_and_ask_gates_are_exact_and_independent():
    routes = {(m, r.path): r for r in http_routes(app.routes) for m in (r.methods or [])}
    off = LaunchSettings(True, ('testserver',))
    model = LaunchSettings(True, ('testserver',), model_read_enabled=True)
    ask = LaunchSettings(True, ('testserver',), ask_ai_enabled=True)
    for key in [('GET', '/workspaces/{workspace_id}/model-summary'), ('GET', '/workspaces/{workspace_id}/relationships')]:
        assert not permitted(routes[key], key[0], off)
        assert permitted(routes[key], key[0], model)
        assert not permitted(routes[key], key[0], ask)
    for key in [('GET', '/workspaces/{workspace_id}/questions/answers'), ('POST', '/workspaces/{workspace_id}/questions')]:
        assert not permitted(routes[key], key[0], model)
        assert permitted(routes[key], key[0], ask)
    for key in [('POST', '/workspaces/{workspace_id}/relationships/discover'), ('POST', '/workspaces/{workspace_id}/relationships/candidates/{candidate_id}/confirm')]:
        assert not permitted(routes[key], key[0], model)


@pytest.mark.parametrize('path', ['/model-summary', '/relationships', '/questions/readiness', '/questions/answers'])
def test_wrong_owner_rejected_before_service(client, monkeypatch, path):
    @contextmanager
    def transaction():
        yield SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchone=lambda: None))
    monkeypatch.setattr(database, 'repository_transaction', transaction)
    monkeypatch.setattr(workspace_model, 'model_summary', lambda *a: pytest.fail('wrong owner reached model'))
    monkeypatch.setattr(workspace_questions.service, 'readiness', lambda *a: pytest.fail('wrong owner reached AI'))
    monkeypatch.setattr(workspace_questions.service, 'history', lambda *a: pytest.fail('wrong owner reached history'))
    launch = LaunchSettings(True, ('testserver',), model_read_enabled=True, ask_ai_enabled=True)
    with TestClient(PublicLaunchBoundary(app, settings=launch, routes=app.routes), base_url='https://testserver') as c:
        response = c.get('/workspaces/99' + path)
    assert response.status_code == 404
    assert 'owner:7' not in response.text


def test_ask_write_still_requires_csrf(client, monkeypatch):
    monkeypatch.setattr(workspace_questions.service, 'ask', lambda *a: pytest.fail('CSRF bypass'))
    launch = LaunchSettings(True, ('testserver',), ask_ai_enabled=True)
    with TestClient(PublicLaunchBoundary(app, settings=launch, routes=app.routes), base_url='https://testserver') as c:
        assert c.post('/workspaces/1/questions', json={'question': 'How many records?'}).status_code == 403


def test_disabled_ai_never_calls_provider(client, monkeypatch):
    monkeypatch.setattr(workspace_questions.service, 'ask', lambda *a: pytest.fail('disabled provider called'))
    launch = LaunchSettings(True, ('testserver',))
    with TestClient(PublicLaunchBoundary(app, settings=launch, routes=app.routes), base_url='https://testserver') as c:
        assert c.post('/workspaces/1/questions', json={'question': 'How many records?'}).status_code == 403
