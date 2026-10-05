import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.identity import get_current_user
from app.api import identity, workspaces, files
from app.services import file_service

USER = {"user_id": 42, "owner_key": "dev:test", "display_name": "Test"}
WS = (7, "Sales", None, "dev:test", "ACTIVE", None, None)
DS = (13, "Orders", None, "dev:test", "ACTIVE", None, None, 7)

@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: USER
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_current_user, None)


def test_workspace_create_list_get_uses_current_owner(client, monkeypatch):
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return WS, True
    monkeypatch.setattr(workspaces, 'create_workspace', create)
    monkeypatch.setattr(workspaces, 'list_workspaces_by_owner', lambda owner: [WS] if owner == USER['owner_key'] else [])
    monkeypatch.setattr(workspaces, 'get_workspace_by_id', lambda workspace_id: WS)
    response = client.post('/workspaces', json={'workspace_name': 'Sales', 'owner': 'spoofed'})
    assert response.status_code == 200
    assert response.json()['workspace_id'] == 7
    assert captured['owner'] == USER['owner_key']
    assert client.get('/workspaces').json()['workspaces'][0]['workspace_id'] == 7
    assert client.get('/workspaces/7').json()['workspace_id'] == 7


def test_dataset_create_list_get_scope(client, monkeypatch):
    monkeypatch.setattr(workspaces, 'get_workspace_by_id', lambda workspace_id: WS)
    monkeypatch.setattr(workspaces, 'get_datasets_by_workspace', lambda **kwargs: [DS])
    monkeypatch.setattr(workspaces, 'get_dataset_by_workspace', lambda **kwargs: DS if kwargs['dataset_id'] == 13 else None)
    def create(**kwargs):
        assert kwargs['owner'] == USER['owner_key']
        assert kwargs['workspace_id'] == 7
        return {'dataset_id': 13, 'workspace_id': 7}
    monkeypatch.setattr(workspaces, 'get_or_create_dataset', create)
    assert client.post('/workspaces/7/datasets', json={'dataset_name': 'Orders', 'owner': 'spoofed'}).json()['dataset_id'] == 13
    assert client.get('/workspaces/7/datasets').json()['datasets'][0]['dataset_id'] == 13
    assert client.get('/workspaces/7/datasets/13').json()['workspace_id'] == 7
    assert client.get('/workspaces/7/datasets/99').status_code == 404


@pytest.mark.parametrize('method,path,payload', [('get','/workspaces/7',None), ('get','/workspaces/7/datasets',None), ('post','/workspaces/7/datasets',{'dataset_name':'Orders'})])
def test_workspace_access_denied(client, monkeypatch, method, path, payload):
    monkeypatch.setattr(workspaces, 'get_workspace_by_id', lambda workspace_id: (7,'Private',None,'other'))
    kwargs = {} if payload is None else {'json': payload}
    assert client.request(method, path, **kwargs).status_code == 403


def test_upload_context_enforces_user_owner(monkeypatch):
    monkeypatch.setattr(file_service, 'get_dataset_by_workspace', lambda **kwargs: DS)
    monkeypatch.setattr(file_service, 'get_workspace_by_id', lambda workspace_id: WS)
    monkeypatch.setattr(file_service, 'get_user_by_id', lambda user_id: {'owner_key': 'other'})
    with pytest.raises(ValueError, match='access denied'):
        file_service.validate_upload_context(99, 7, 13)


def test_identity_disabled_by_default(monkeypatch):
    monkeypatch.delenv('APP_ENV', raising=False)
    monkeypatch.delenv('ENABLE_DEV_IDENTITY', raising=False)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        identity.get_current_user()
    assert error.value.status_code == 503


def test_dev_identity_uses_database_user(monkeypatch):
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.setenv('ENABLE_DEV_IDENTITY','true')
    monkeypatch.setenv('DEV_USER_KEY','test')
    def get_user(key, name):
        assert key == 'dev:test'
        return USER
    monkeypatch.setattr(identity, 'get_or_create_dev_user', get_user)
    assert identity.get_current_user()['user_id'] == 42


def test_me(client):
    assert client.get('/me').json()['user_id'] == 42


def test_session_api_uses_principal_and_id_only(client, monkeypatch):
    def initiate(**kwargs):
        assert kwargs['user_id'] == 42
        return {'upload_request_id': 123, 'session_status': 'INITIATED'}
    monkeypatch.setattr(files, 'initiate_upload_session', initiate)
    payload = {'workspace_id':7,'dataset_id':13,'file_name':'a.csv','file_size':10}
    assert client.post('/files/initiate',json=payload).json()['upload_request_id'] == 123
    assert client.post('/files/initiate',json={**payload,'user_id':99}).status_code == 403
    monkeypatch.setattr(files, 'complete_upload_session', lambda upload_id, user_id: {'upload_request_id':upload_id,'session_status':'COMPLETED'})
    assert client.post('/files/complete',json={'upload_request_id':123}).json()['session_status'] == 'COMPLETED'
    assert client.post('/files/complete',json={'object_key':'arbitrary','expected_file_size':10,'user_id':42}).status_code == 422

@pytest.mark.parametrize('filename', ['../a.csv', 'folder/a.csv', 'a.xlsx', 'folder\\a.csv'])
def test_initiate_rejects_invalid_filename_before_signing(monkeypatch, filename):
    from app.services import upload_session_service as service
    monkeypatch.setattr(service, 'validate_upload_context', lambda *args: None)
    monkeypatch.setattr(service, 'generate_presigned_upload_url', lambda **kwargs: pytest.fail('Invalid filename must not reach signing'))
    with pytest.raises(ValueError, match='filename'):
        service.initiate_upload_session(42,7,13,filename,10)
