"""Real local sessions and current ownership through the public HTTP boundary.

The explicit test wrapper exercises HTTP policy; production startup validation is
tested separately. Fixture-only real stage setup is not public processing access.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.security.public_launch import PublicLaunchBoundary, LaunchSettings
from tests.test_task21h156_product_postgres import context
from tests.test_task20_processing_postgres import pipeline
from tests.test_processing_jobs_postgres import source
from tests.test_rule_approval_postgres import pg


@pytest.fixture
def public(context):
    c=context
    with TestClient(PublicLaunchBoundary(app,settings=LaunchSettings(True,('testserver',)),routes=app.routes),base_url='https://testserver') as client:
        client.cookies.set('datarise_session',c['client'].cookies.get('datarise_session'))
        from tests.http_client import enable_csrf
        enable_csrf(client)
        yield {**c,'client':client}


def test_public_csv_first_existing_source_journey(public):
    c=public;client=c['client']
    assert client.get('/me').status_code==200
    assert client.get('/workspaces').status_code==200
    assert client.get(f"/workspaces/{c['w']}/datasets/{c['d']}").status_code==200
    assert client.get(c['base']+'/capabilities').json()['csv_upload'] is False
    assert client.get(c['base']+'/jobs').status_code==200
    result=client.post(c['base']+f"/uploads/{c['src'][2]}/csv-preview",json={'mode':'quality','csv_base64':c['body']})
    assert result.status_code==200,result.text
    assert (result.json()['valid_rows'],result.json()['rejected_rows'])==(2,0)
    assert result.json()['publication_authorized'] is False
    assert client.get(f"/workspaces/{c['w']}/datasets/{c['d']}/processing").json()['summary']['total']==1


@pytest.mark.parametrize('fault',['workspace','dataset','ownership','disabled'])
def test_current_owner_or_identity_revocation_blocks_public_reads(public,fault):
    c=public;w,d=c['w'],c['d']
    if fault=='workspace':w+=10000
    elif fault=='dataset':
        with c['src'][0]() as conn:
            d=conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES(%s,'Other','dev:other') RETURNING dataset_id",(w,)).fetchone()[0]
    else:
        with c['src'][0]() as conn:
            if fault=='ownership':conn.execute("UPDATE datasets SET owner='dev:other' WHERE dataset_id=%s",(d,))
            else:conn.execute("UPDATE users SET account_status='DISABLED' WHERE user_id=%s",(c['src'][1],))
    for suffix in ['', '/processing','/profile','/analytics/readiness','/product/jobs']:
        r=c['client'].get(f'/workspaces/{w}/datasets/{d}'+suffix)
        assert r.status_code==(401 if fault=='disabled' else 404),(suffix,r.text)


def test_workspace_dataset_list_excludes_inconsistent_owners(public):
    c=public
    with c['src'][0]() as conn:
        other=conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES(%s,'Secret sibling','dev:other') RETURNING dataset_id",(c['w'],)).fetchone()[0]
    r=c['client'].get(f"/workspaces/{c['w']}/datasets")
    assert r.status_code==200,r.text
    assert {d['dataset_id'] for d in r.json()['datasets']}=={c['d']}
    assert str(other) not in {str(d['dataset_id']) for d in r.json()['datasets']}
    assert 'Secret sibling' not in r.text


def test_authenticated_direct_execution_and_upload_cannot_create_work(public):
    c=public;client=c['client']
    def counts():
        with c['src'][0]() as conn:
            return tuple(conn.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ['processing_jobs','processing_attempts','processing_stage_receipts','upload_requests'])
    before=counts()
    for url in [f"/files/uploads/{c['src'][2]}/process",f"/files/uploads/{c['src'][2]}/continue",'/files/initiate','/files/complete',f"/workspaces/{c['w']}/datasets/{c['d']}/processing/pending",f"/workspaces/{c['w']}/datasets/{c['d']}/analytics/refresh"]:
        r=client.post(url+'?approved=true',json={'deployment_approved':True,'user_id':c['src'][1]})
        assert r.status_code==403,(url,r.text)
    assert counts()==before


def test_public_logout_revokes_durable_session(public):
    client=public['client']
    assert client.get('/me').status_code==200
    assert client.post('/auth/logout').status_code==200
    assert client.get('/me').status_code==401
    with public['src'][0]() as conn:
        assert conn.execute('SELECT count(*) FROM auth_sessions WHERE revoked_at IS NOT NULL').fetchone()[0]>=1
