"""Offline HTTP security boundaries, response allowlists and deployment config."""
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.identity import get_current_user
from app.api import product_preview as api
from app.services import product_preview_service as service
from tools.website_config_check import check

BASE='/workspaces/1/datasets/2/product'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.setenv('AUTH_MODE','session')
    monkeypatch.setenv('AUTH_SECRET','synthetic-product-api-key-'*2)
    app.dependency_overrides[get_current_user]=lambda:{'user_id':7,'owner_key':'synthetic'}
    with TestClient(app) as c:
        from tests.http_client import enable_csrf
        enable_csrf(c)
        yield c
    app.dependency_overrides.pop(get_current_user,None)


def test_capability_response_filters_internal_metadata(client,monkeypatch):
    monkeypatch.setattr(service,'capabilities',lambda *a:dict(workspace_id=1,dataset_id=2,warehouse='NOT_CONFIGURED',secret='SYNTHETIC_PRIVATE',
        warehouse_models=[dict(model_id=1,version_id=2,name='Orders',version_number=1,storage_path='SYNTHETIC_PRIVATE')]))
    r=client.get(BASE+'/capabilities')
    assert r.status_code==200 and 'SYNTHETIC_PRIVATE' not in r.text
    assert r.json()['processing_submission']=='DISABLED' and r.json()['xlsx']=='UNSUPPORTED'
    assert r.headers['cache-control']=='no-store, private'


def test_authentication_and_csrf_precede_product_service(client,monkeypatch):
    monkeypatch.setattr(service,'authorize',lambda *a:pytest.fail('unauthenticated computation'))
    app.dependency_overrides.pop(get_current_user)
    assert client.post(BASE+'/uploads/3/csv-preview',json={}).status_code==401
    app.dependency_overrides[get_current_user]=lambda:{'user_id':7,'owner_key':'synthetic'}
    client.headers.pop('X-CSRF-Token')
    assert client.post(BASE+'/uploads/3/csv-preview',json={}).status_code==403


def test_authorization_precedes_reading_and_decoding_request(client,monkeypatch):
    def deny(*a):raise PermissionError('SYNTHETIC_PRIVATE')
    monkeypatch.setattr(service,'authorize',deny)
    monkeypatch.setattr(api.CsvPreviewRequest,'model_validate_json',lambda *a:pytest.fail('unauthorized body decoded'))
    response=client.post(BASE+'/uploads/3/csv-preview',content=b'not-json')
    assert response.status_code==404 and 'SYNTHETIC_PRIVATE' not in response.text


@pytest.mark.parametrize('extra',[{'owner':'other'},{'rules':[]},{'source_key':'private'},{'publication_authorized':True}])
def test_clients_cannot_supply_contract_or_authority(client,monkeypatch,extra):
    monkeypatch.setattr(service,'authorize',lambda *a:None)
    monkeypatch.setattr(service,'csv_preview',lambda *a:pytest.fail('unsafe request reached preview'))
    result=client.post(BASE+'/uploads/3/csv-preview',json={'mode':'exact','csv_base64':'YWJj',**extra})
    assert result.status_code==422 and all(x not in result.text for x in ['private','other'])


def test_http_and_schema_bounds_are_independent(client,monkeypatch):
    monkeypatch.setattr(service,'authorize',lambda *a:None)
    assert client.post(BASE+'/uploads/3/csv-preview',content=b'a'*(api.MAX_REQUEST_BYTES+1)).status_code==413
    assert client.post(BASE+'/uploads/3/csv-preview',json={'mode':'invented','csv_base64':'x'}).status_code==422


@pytest.mark.parametrize('identity',['99999999999999999999999999999','SYNTHETIC_PRIVATE'])
def test_invalid_resource_id_is_sanitized_before_lookup(client,monkeypatch,identity):
    monkeypatch.setattr(service,'capabilities',lambda *a:pytest.fail('invalid ID reached SQL'))
    result=client.get(f'/workspaces/{identity}/datasets/2/product/capabilities')
    assert result.status_code==422 and 'SYNTHETIC_PRIVATE' not in result.text


@pytest.mark.parametrize('failure,status',[(service.SourceMismatch,409),(service.PreviewBusy,429),(RuntimeError,503)])
def test_errors_never_echo_driver_or_source_values(client,monkeypatch,failure,status):
    monkeypatch.setattr(service,'authorize',lambda *a:None)
    def fail(*a):raise failure('SYNTHETIC_PRIVATE')
    monkeypatch.setattr(service,'csv_preview',fail)
    response=client.post(BASE+'/uploads/3/csv-preview',json={'mode':'quality','csv_base64':base64.b64encode(b'a').decode()})
    assert response.status_code==status and 'SYNTHETIC_PRIVATE' not in response.text


def test_preview_response_omits_artifact_and_token_fields(client,monkeypatch):
    monkeypatch.setattr(service,'authorize',lambda *a:None)
    monkeypatch.setattr(service,'csv_preview',lambda *a:dict(workspace_id=1,dataset_id=2,status='PREVIEW',total_rows=1,
        valid_rows=1,rejected_rows=0,valid_parquet='SYNTHETIC_PRIVATE',lease_token='SYNTHETIC_PRIVATE'))
    r=client.post(BASE+'/uploads/3/csv-preview',json={'mode':'quality','csv_base64':'YQ=='})
    assert r.status_code==200 and 'SYNTHETIC_PRIVATE' not in r.text
    assert r.json()['publication_authorized'] is False


def test_public_configuration_gate_is_offline_and_never_approval():
    env=dict(APP_ENV='production',AUTH_MODE='session',AUTH_COOKIE_SECURE='true',AUTH_SECRET='synthetic-only-'*4,
        AUTH_FRONTEND_URL='https://website.example',AUTH_BACKEND_URL='https://api.example',
        DATARISE_PROCESSING_DEPLOYMENT_APPROVED='false',DATARISE_PROCESSING_READERS='false',DATARISE_DISPATCH_APPROVED='false')
    assert check(env)==dict(configuration_checks_passed=True,hosting_approved=False,processing_approved=False,reasons=[])
    for field,value in [('APP_ENV','development'),('AUTH_MODE','development'),('ENABLE_DEV_IDENTITY','true'),
        ('AUTH_COOKIE_SECURE','false'),('AUTH_BACKEND_URL','http://api.example'),('AUTH_SECRET',''),('DATARISE_DISPATCH_APPROVED','true')]:
        assert not check({**env,field:value})['configuration_checks_passed']
