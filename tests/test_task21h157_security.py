"""Direct HTTP launch policy tests. No AWS, pipeline execution or database."""
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.main import app
from app.security.public_launch import PublicLaunchBoundary, LaunchSettings, launch_settings, permitted, POLICY, http_routes
from tools.public_route_inventory import inventory


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.setenv('AUTH_MODE','session')
    monkeypatch.setenv('AUTH_SECRET','synthetic-public-launch-test-key-'*2)
    wrapper=PublicLaunchBoundary(app,settings=LaunchSettings(True,('testserver',)),routes=app.routes)
    with TestClient(wrapper,base_url='https://testserver',raise_server_exceptions=False) as c: yield c


BLOCKED=[(method,r.path) for r in http_routes(app.routes) for method in sorted(r.methods or []) if not permitted(r,method)]


@pytest.mark.parametrize('method,path',BLOCKED)
def test_every_unapproved_registered_route_is_denied_before_dependencies(client,monkeypatch,method,path):
    from app.api.identity import get_current_user
    app.dependency_overrides[get_current_user]=lambda:pytest.fail('Blocked request ran authentication/handler')
    try:
        import re
        url=re.sub(r'\{[^}]+\}','1',path)
        response=client.request(method,url,content=b'{"deployment_approved":true}')
        assert response.status_code==403,(method,path,response.text)
        assert response.headers['x-frame-options']=='DENY'
    finally: app.dependency_overrides.pop(get_current_user,None)


@pytest.mark.parametrize('method,path',[('GET','/files/uploads/1/process'),('PUT','/auth/login'),('HEAD','/me'),('TRACE','/me'),('POST','/unknown'),('GET','/docs/'),('GET','/workspaces/1/datasets/2/product/jobs/extra')])
def test_methods_and_prefixes_do_not_grant_authority(client,method,path):
    assert client.request(method,path).status_code==403


def test_safe_routes_still_require_real_session_and_csrf(client):
    assert client.get('/me').status_code==401
    assert client.get('/workspaces/1/datasets/2/product/jobs').status_code==401
    assert client.post('/auth/logout').status_code==403
    assert client.get('/auth/logout').status_code==403
    token=client.get('/auth/csrf').json()['csrf_token']
    assert client.post('/auth/logout',headers={'origin':'https://attacker.example','x-csrf-token':token}).status_code==403
    response=client.post('/auth/logout',headers={'origin':'http://localhost:5173','x-csrf-token':token})
    assert response.status_code==200 and response.json()['status']=='SIGNED_OUT'


def test_headers_and_tls_host_forwarding(client):
    response=client.get('/auth/config')
    assert response.status_code==200
    for name in ['cache-control','x-frame-options','content-security-policy','strict-transport-security','referrer-policy','permissions-policy']:
        assert name in response.headers
    assert client.get('/auth/config',headers={'host':'evil.example','x-forwarded-host':'testserver','x-forwarded-proto':'https'}).status_code==400
    assert client.get('http://testserver/auth/config',headers={'x-forwarded-proto':'https'}).status_code==400
    assert client.get('/auth/config',headers={'x-forwarded-host':'evil.example'}).status_code==200
    assert client.get('/auth/config',headers=[('host','testserver'),('host','evil.example')]).status_code==400


@pytest.mark.parametrize('identity',['0','9007199254740992','not-an-id'])
def test_public_resource_ids_are_bounded_before_scope_query(client,monkeypatch,identity):
    from app.api import identity as auth_identity
    from app.services import product_preview_service
    monkeypatch.setattr(auth_identity,'get_current_user',lambda *a:{'user_id':1,'owner_key':'synthetic'})
    monkeypatch.setattr(product_preview_service,'authorize',lambda *a:pytest.fail('Invalid identifier reached database scope'))
    assert client.get(f'/workspaces/1/datasets/{identity}/product/jobs').status_code==404


def test_cors_preflight_is_bound_to_allowed_method_and_origin(client):
    headers={'origin':'http://localhost:5173','access-control-request-method':'POST'}
    response=client.options('/auth/login',headers=headers)
    assert response.status_code==200 and response.headers['access-control-allow-origin']=='http://localhost:5173'
    assert client.options('/files/uploads/1/process',headers=headers).status_code==403
    assert client.options('/auth/login',headers={**headers,'origin':'https://evil.example'}).status_code==403


def test_size_validation_and_errors_do_not_echo_inputs(client,monkeypatch):
    from app.auth import service
    monkeypatch.setattr(service,'login',lambda *a:(_ for _ in ()).throw(RuntimeError('SYNTHETIC_PRIVATE')))
    token=client.get('/auth/csrf').json()['csrf_token']
    h={'origin':'http://localhost:5173','x-csrf-token':token}
    assert client.post('/auth/login',content=b'x'*65537,headers=h).status_code==413
    assert client.post('/auth/login',content=(b'x'*40000 for _ in range(2)),headers=h).status_code==413
    r=client.post('/auth/login',json={'email':'preview@example.com','password':'synthetic-password'},headers=h)
    assert r.status_code>=500 and 'SYNTHETIC_PRIVATE' not in r.text
    r=client.post('/auth/login',json={'email':'SYNTHETIC_PRIVATE'},headers=h)
    assert r.status_code==422 and 'SYNTHETIC_PRIVATE' not in r.text


def test_rate_rejections_remain_fail_closed(client,monkeypatch):
    from app.auth import service
    monkeypatch.setattr(service,'login',lambda *a:(_ for _ in ()).throw(service.AuthError('private bucket',429)))
    token=client.get('/auth/csrf').json()['csrf_token']
    r=client.post('/auth/login',json={'email':'preview@example.com','password':'synthetic-password'},headers={'origin':'http://localhost:5173','x-csrf-token':token,'x-forwarded-for':'attacker-supplied'})
    assert r.status_code==429 and 'private bucket' not in r.text


def test_callback_rejects_missing_or_replayed_state_without_exposing_code(client):
    r=client.get('/auth/google/callback?code=SYNTHETIC_PRIVATE',follow_redirects=False)
    assert r.status_code==303 and r.headers['location']=='http://localhost:5173/login?error=google'
    assert 'SYNTHETIC_PRIVATE' not in r.text+r.headers.get('location','')


@pytest.fixture
def production(monkeypatch):
    values=dict(APP_ENV='production',AUTH_MODE='session',ENABLE_DEV_IDENTITY='false',AUTH_COOKIE_SECURE='true',AUTH_SECRET='synthetic-public-session-key-'*2,
        AUTH_FRONTEND_URL='https://site.example.test',AUTH_BACKEND_URL='https://api.example.test',DATARISE_PUBLIC_LAUNCH_MODE='csv-readonly',DATARISE_PUBLIC_HOSTS='api.example.test',
        DATARISE_PROCESSING_DEPLOYMENT_APPROVED='false',DATARISE_PROCESSING_READERS='false',DATARISE_DISPATCH_APPROVED='false')
    for k,v in values.items():monkeypatch.setenv(k,v)
    return values


@pytest.mark.parametrize('key,value',[('APP_ENV','development'),('AUTH_MODE','development'),('ENABLE_DEV_IDENTITY','true'),('AUTH_COOKIE_SECURE','false'),('AUTH_SECRET','short'),('DATARISE_PUBLIC_LAUNCH_MODE','typo'),('DATARISE_PUBLIC_HOSTS','*'),('DATARISE_PUBLIC_HOSTS','other.example.test'),('DATARISE_PROCESSING_DEPLOYMENT_APPROVED','true'),('DATARISE_PROCESSING_READERS','true'),('DATARISE_DISPATCH_APPROVED','true'),('AUTH_FRONTEND_URL','http://site.example.test')])
def test_unsafe_launch_configuration_refuses_boot(production,monkeypatch,key,value):
    monkeypatch.setenv(key,value)
    with pytest.raises(ValueError):launch_settings()


def test_secure_production_configuration_and_cookies(production):
    settings=launch_settings();assert settings.enabled and settings.hosts==('api.example.test',)
    with TestClient(PublicLaunchBoundary(app,settings=settings,routes=app.routes),base_url='https://api.example.test') as c:
        r=c.get('/auth/csrf');assert r.status_code==200
        cookie=r.headers['set-cookie']
        assert '__Host-datarise_csrf=' in cookie and 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=lax' in cookie
        assert c.get('/auth/config').json()['public_launch_mode']=='csv-readonly'


def test_unreviewed_or_replaced_handler_cannot_inherit_a_grant():
    fake=FastAPI()
    @fake.get('/auth/config')
    def innocent_name():return {'unsafe':True}
    with TestClient(PublicLaunchBoundary(fake,settings=LaunchSettings(True,('testserver',)),routes=fake.routes),base_url='https://testserver') as c:
        assert c.get('/auth/config').status_code==403
    assert not any(permitted(r,'GET') for r in fake.routes)


def test_inventory_covers_actual_routes_and_no_authless_product_grants():
    rows=inventory(app)
    assert len(rows)==sum(len(r.methods or []) for r in http_routes(app.routes))
    for r in rows:
        if r['public_allowed'] and not r['path'].startswith('/auth/'):
            assert 'app.api.identity.get_current_user' in r['dependencies']
    assert {(r['method'],r['path']) for r in rows if r['public_allowed']}==set(POLICY)
    Path(os.environ['TEMP'],'task21h157-route-inventory.json').write_text(json.dumps(rows,indent=2))


def test_off_mode_preserves_development_routes():
    fake=FastAPI()
    @fake.post('/legacy')
    def legacy():return {'local':True}
    with TestClient(PublicLaunchBoundary(fake,settings=LaunchSettings(),routes=fake.routes)) as c:
        assert c.post('/legacy').json()=={'local':True}


def test_actual_production_app_boot_has_no_docs_or_development_identity(production):
    import subprocess
    import sys
    code='''
import sys,socket
sys.dont_write_bytecode=True
def blocked(*args,**kwargs): raise AssertionError('Network/DB/SDK prohibited')
connect=socket.socket.connect
resolve=socket.getaddrinfo
def local_connect(self,address):
    if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1','localhost'): return blocked()
    return connect(self,address)
def local_resolve(host,*args,**kwargs):
    if host not in ('127.0.0.1','::1','localhost'): return blocked()
    return resolve(host,*args,**kwargs)
socket.socket.connect=local_connect
socket.getaddrinfo=local_resolve
import boto3,psycopg
boto3.Session=boto3.client=psycopg.connect=blocked
from app.main import app,public_launch
assert public_launch.enabled
assert app.docs_url is app.redoc_url is app.openapi_url is None
from fastapi.testclient import TestClient
with TestClient(app,base_url='https://api.example.test') as client:
    assert client.get('/auth/config').json()['public_launch_mode']=='csv-readonly'
    assert client.get('/me').status_code==401
    assert client.get('/health/db').status_code==403
print('Production HTTP boot checked offline')
'''
    env={**os.environ,'PYTHON_DOTENV_DISABLED':'1','PYTHONDONTWRITEBYTECODE':'1'}
    result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'Production HTTP boot checked offline' in result.stdout
