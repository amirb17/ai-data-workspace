"""Actual sessions/ownership, registered bytes and approved-policy previews."""
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from app.main import app
from app.auth import service as auth
from app.services import product_preview_service as product
from tests.test_rule_approval_postgres import pg
from tests.test_processing_jobs_postgres import source
from tests.test_task20_processing_postgres import pipeline, approve, new_delivery


@pytest.fixture
def context(pipeline,monkeypatch,request):
    monkeypatch.setenv('APP_ENV','development');monkeypatch.setenv('AUTH_MODE','session')
    monkeypatch.setenv('AUTH_SECRET','synthetic-product-session-key-'*2)
    src,storage,run=pipeline
    variant=getattr(request,'param',False)
    raw=b'id,city,amount\n1,A,10\n2,A,20\n'
    if variant=='dq':
        from tests.task21h153_support import CSV
        raw=CSV
        upload,_=new_delivery(pipeline,raw)
        src=(src[0],src[1],upload)
    run('BRONZE',upload=src[2])
    if variant:
        from app.services import business_rule_service as rules,processing_context_service as scope
        from app.schemas.business_rules import BusinessRuleAnswer
        answers=[('id','NOT_NULL','YES'),('id','UNIQUE','YES'),('city','NOT_NULL','YES'),('amount','DATA_TYPE','DECIMAL'),
            ('amount','NOT_NULL','YES'),('amount','ALLOW_NEGATIVE','NO'),('ordered_on','DATA_TYPE','DATE'),('ordered_on','NOT_NULL','YES')] if variant=='dq' else [('amount','DATA_TYPE','DECIMAL_EXACT:P20:S2')]
        monkeypatch.setattr(rules,'generate_rule_suggestions',lambda _:[dict(column_name=c,suggested_rule_type=k,options=['DECIMAL'] if a.startswith('DECIMAL_EXACT') else [a]) for c,k,a in answers])
        with src[0]() as conn:
            association,w,d=conn.execute('''SELECT f.dataset_version_file_id,d.workspace_id,d.dataset_id
                FROM dataset_version_files f JOIN dataset_versions v USING(dataset_version_id)
                JOIN physical_files p ON p.file_id=f.file_id AND p.schema_hash=v.schema_hash JOIN datasets d USING(dataset_id)
                WHERE p.file_id=(SELECT file_id FROM upload_requests WHERE upload_id=%s)''',(src[2],)).fetchone()
        actor={'user_id':src[1],'owner_key':'dev:test'}
        with scope.rule_mutation(association,actor,0,w,d): rules.submit_business_rule_answers(association,[BusinessRuleAnswer(column_name=c,rule_type=k,answer=a) for c,k,a in answers])
        with scope.rule_mutation(association,actor,1,w,d): assert rules.finalize_business_rules(association)['finalized']
    else: association=approve(src)
    with src[0]() as conn:
        w,d=conn.execute('SELECT workspace_id,dataset_id FROM upload_requests WHERE upload_id=%s',(src[2],)).fetchone()
        token=auth.issue_session(conn.cursor(row_factory=dict_row),src[1])
    from tests.http_client import enable_csrf
    with TestClient(app) as client:
        enable_csrf(client)
        client.cookies.set('datarise_session',token)
        yield dict(src=src,storage=storage,run=run,client=client,w=w,d=d,association=association,
                   base=f'/workspaces/{w}/datasets/{d}/product',body=base64.b64encode(raw).decode())


def test_real_session_owned_csv_preview_no_publication(context):
    c=context;client=c['client'];url=c['base']
    assert client.get(url+'/capabilities').json()['processing_submission']=='DISABLED'
    response=client.post(url+f"/uploads/{c['src'][2]}/csv-preview",json={'mode':'quality','csv_base64':c['body']})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['status']=='PREVIEW' and (result['total_rows'],result['valid_rows'],result['rejected_rows'])==(2,2,0)
    assert result['columns']==['id','city','amount'] and result['publication_authorized'] is False
    jobs=client.get(url+'/jobs').json()['jobs']
    assert len(jobs)==1 and set(jobs[0])=={'job_id','operation','status','attempt_count'}
    with c['src'][0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM data_quality_runs').fetchone()[0]==0
    assert 'lease_token' not in response.text and 's3://' not in response.text


@pytest.mark.parametrize('fault',['workspace','dataset','upload','ownership','disabled','archived','incomplete'])
def test_cross_scope_or_revoked_access_stops_before_compute(context,monkeypatch,fault):
    c=context;url=c['base'];upload=c['src'][2]
    if fault=='workspace':url=f"/workspaces/{c['w']+100}/datasets/{c['d']}/product"
    elif fault=='dataset':
        with c['src'][0]() as conn:other=conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES(%s,'Sibling','dev:test') RETURNING dataset_id",(c['w'],)).fetchone()[0]
        url=f"/workspaces/{c['w']}/datasets/{other}/product"
    elif fault=='upload':upload+=100
    else:
        with c['src'][0]() as conn:
            if fault=='ownership':conn.execute("UPDATE datasets SET owner='dev:other' WHERE dataset_id=%s",(c['d'],))
            elif fault=='disabled':conn.execute("UPDATE users SET account_status='DISABLED' WHERE user_id=%s",(c['src'][1],))
            elif fault=='archived':conn.execute('UPDATE upload_requests SET archived_at=clock_timestamp(),archived_by=%s WHERE upload_id=%s',(c['src'][1],upload))
            else:conn.execute("UPDATE upload_requests SET status='UPLOADING' WHERE upload_id=%s",(upload,))
    def unexpected(*a,**kw): raise AssertionError('Unauthorized CSV decoded')
    # Replace only this module's adapter: mutating stdlib base64 would also
    # intercept the real CSRF signature verification exercised by these tests.
    monkeypatch.setattr(product,'base64',SimpleNamespace(b64decode=unexpected))
    assert c['client'].post(url+f'/uploads/{upload}/csv-preview',json={'mode':'quality','csv_base64':c['body']}).status_code==(401 if fault=='disabled' else 404)


def test_registered_checksum_and_approved_policy_cannot_be_overridden(context):
    c=context;url=c['base']+f"/uploads/{c['src'][2]}/csv-preview"
    assert c['client'].post(url,json={'mode':'quality','csv_base64':'eA=='}).status_code==409
    response=c['client'].post(url,json={'mode':'exact','csv_base64':c['body']})
    assert response.json()['reason']=='EXACT_APPROVED_POLICY_REQUIRED'
    with c['src'][0]() as conn:conn.execute('UPDATE dataset_version_files SET applied_rule_version=NULL WHERE dataset_version_file_id=%s',(c['association'],))
    assert c['client'].post(url,json={'mode':'quality','csv_base64':c['body']}).json()['reason']=='RULE_APPROVAL_REQUIRED'


@pytest.mark.parametrize('context',[True],indirect=True)
def test_exact_preview_uses_persisted_approved_contract(context):
    c=context
    response=c['client'].post(c['base']+f"/uploads/{c['src'][2]}/csv-preview",json={'mode':'exact','csv_base64':c['body']})
    assert response.status_code==200,response.text
    assert response.json()['valid_rows']==2 and response.json()['rule_version']==1
    assert response.json()['publication_authorized'] is False


@pytest.mark.parametrize('context',['dq'],indirect=True)
def test_quality_preview_returns_real_quarantine_reason_counts(context):
    c=context
    result=c['client'].post(c['base']+f"/uploads/{c['src'][2]}/csv-preview",json={'mode':'quality','csv_base64':c['body']})
    assert result.status_code==200,result.text
    value=result.json()
    assert (value['total_rows'],value['valid_rows'],value['rejected_rows'])==(8,2,6)
    issues={(x['column'],x['rule']):x['count'] for x in value['issues']}
    assert issues[('id','UNIQUE')]==2 and issues[('ordered_on','INVALID_DATETIME')]==1
    assert value['publication_authorized'] is False


def test_session_revocation_is_enforced_without_identity_override(context):
    c=context
    with c['src'][0]() as conn:conn.execute('UPDATE auth_sessions SET revoked_at=clock_timestamp()')
    assert c['client'].get(c['base']+'/capabilities').status_code==401


def test_computation_capacity_is_bounded_and_released(context):
    c=context;url=c['base']+f"/uploads/{c['src'][2]}/csv-preview"
    assert product._compute.acquire(blocking=False)
    try:assert c['client'].post(url,json={'mode':'quality','csv_base64':c['body']}).status_code==429
    finally:product._compute.release()
    assert c['client'].post(url,json={'mode':'quality','csv_base64':c['body']}).json()['status']=='PREVIEW'


def test_real_login_and_owned_product_workflow(context):
    from app.auth.security import password_hash
    c=context;client=c['client']
    with c['src'][0]() as conn:
        conn.execute("UPDATE users SET email='product@example.com',normalized_email='product@example.com',email_verified=true,password_hash=%s WHERE user_id=%s",(password_hash('synthetic product passphrase'),c['src'][1]))
    client.cookies.delete('datarise_session')
    login=client.post('/auth/login',json={'email':'product@example.com','password':'synthetic product passphrase'})
    assert login.status_code==200
    assert client.get('/me').json()['user_id']==c['src'][1]
    assert client.get(c['base']+'/capabilities').status_code==200
    assert client.post(c['base']+f"/uploads/{c['src'][2]}/csv-preview",json={'mode':'quality','csv_base64':c['body']}).json()['status']=='PREVIEW'


def test_verified_gold_assessment_uses_only_server_model_and_injected_reader(context,monkeypatch):
    from app.services.incremental_policy_service import save_policy
    from app.schemas.incremental import LoadPolicyRequest
    from app.services import warehouse_model_service as models,warehouse_modeling_service as modeling
    from app.schemas.warehouse import ModelCreate,VersionCreate,SourceMapping
    from app.schemas.warehouse_modeling import ModelingRequest
    from app.config import S3_BUCKET_NAME
    from app.storage.versioned_artifacts import VersionedArtifacts
    c=context;actor={'user_id':c['src'][1],'owner_key':'dev:test'}
    with c['src'][0]() as conn:version=conn.execute('SELECT dataset_version_id FROM dataset_version_files WHERE dataset_version_file_id=%s',(c['association'],)).fetchone()[0]
    save_policy(c['w'],c['d'],actor,LoadPolicyRequest(dataset_version_id=version,expected_policy_version=0,load_strategy='APPEND',business_keys=[],schema_evolution_policy='STRICT'))
    c['run']('SILVER');c['run']('GOLD')
    monkeypatch.setenv('WAREHOUSE_ENABLED','true');monkeypatch.setenv('WAREHOUSE_PROVIDER','bigquery')
    m=models.create_model(c['w'],actor,ModelCreate(display_name='Orders'))['warehouse_model_id']
    v=models.create_version(c['w'],m,actor,VersionCreate(expected_version=0))['warehouse_model_version_id']
    models.add_source(c['w'],m,v,actor,SourceMapping(source_dataset_id=c['d']))
    with c['src'][0]() as conn:p=conn.execute('SELECT policy_id FROM dataset_load_policies').fetchone()[0]
    modeling.configure_version(c['w'],m,v,actor,ModelingRequest(source_policy_id=p,source_rule_version=1,
        configuration=dict(model_type='FACT',grain=dict(description='One order',identity_columns=['id']),attributes=['city'])))
    models.approve_version(c['w'],m,v,actor)
    path=c['base']+f"/uploads/{c['src'][2]}/gold-assessment?model_id={m}&version_id={v}"
    assert c['client'].get(path).json()['reason']=='TRUSTED_GOLD_READER_UNAVAILABLE'
    result=product.gold_assessment(c['w'],c['d'],c['src'][2],m,v,actor,read_artifact=VersionedArtifacts(c['storage'],S3_BUCKET_NAME).read)
    assert result['status']=='ASSESSED' and result['rows']==2
    assert c['client'].get(c['base']+'/capabilities').json()['warehouse_models'][0]['version_id']==v
