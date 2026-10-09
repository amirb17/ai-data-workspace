"""Trusted-state analytics, real PostgreSQL transactions and immutable Parquet."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pandas as pd
import pytest
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline
from tests.test_append_execution import append_pipeline, apply
from tests.test_incremental_foundation import second_delivery
from app.services import dataset_analytics_service as service
from app.services import append_application_service as execution
from app.processing.cumulative_gold import build_cumulative_gold
from app.storage.cumulative_gold_artifacts import CumulativeGoldArtifacts
from app.services.delivery_lifecycle_service import archive_delivery


@pytest.fixture
def gold(append_pipeline):
    pipe,policy,patch = append_pipeline
    apply(append_pipeline)
    store = CumulativeGoldArtifacts(execution.IncrementalArtifacts().client)
    patch.setattr(service,'CumulativeGoldArtifacts',lambda:store)
    return append_pipeline,store


def refresh(gold):
    db = gold[0][0][0]
    return service.refresh_analytics(db[2],db[3],db[1])


def status(gold):
    db = gold[0][0][0]
    return service.readiness(db[2],db[3],db[1])


@pytest.mark.parametrize('mode', ['APPEND','UPSERT','SNAPSHOT'])
def test_current_input_excludes_operational_and_inactive(mode):
    frame = pd.DataFrame({'id':[1,2], 'amount':[10,20], '_datarise_active':[True,False], '_datarise_origin_upload_id':[3,4]})
    p = {'load_strategy':mode,'schema_columns':[{'name':'id'},{'name':'amount'}], 'business_keys':['id']}
    artifacts = build_cumulative_gold(frame,p)
    base = artifacts[0][0]
    assert list(base)==['id','amount']
    assert base.amount.sum() == (10 if mode=='SNAPSHOT' else 30)
    assert all(not c['column_name'].startswith('_') for _,a in artifacts for c in a['columns'])


def test_empty_active_state_builds_valid_gold():
    frame = pd.DataFrame({'id':pd.Series([1],dtype='int64'),'amount':[4], '_datarise_active':[False]})
    artifacts = build_cumulative_gold(frame,{'load_strategy':'SNAPSHOT','schema_columns':[{'name':'id'},{'name':'amount'}],'business_keys':['id']})
    assert artifacts[0][0].empty and all(a.empty for a,_ in artifacts)


def test_explicit_numeric_key_not_a_measure_and_name_only_time_excluded():
    p={'load_strategy':'UPSERT','schema_columns':[{'name':c} for c in ['sku','amount','event_time']], 'business_keys':['sku']}
    artifacts=build_cumulative_gold(pd.DataFrame({'sku':[101,102],'amount':[10,20],'event_time':['unknown','unknown']}),p)
    assert not any('sku_' in c['column_name'] for _,a in artifacts for c in a['columns'])
    assert not any(a['time_grain'] for _,a in artifacts)


@pytest.mark.parametrize('field', ['gold_run_id','source_state_id','dependencies','artifact_type'])
def test_catalog_ownership_tamper_rejected(field):
    import io,json,hashlib
    objects={}
    class Store:
        def get_object(self,Bucket,Key): return {'Body':io.BytesIO(objects[Key])}
        def put_object(self,Bucket,Key,Body,**kw):
            assert Key not in objects
            objects[Key]=Body
    store=CumulativeGoldArtifacts(Store())
    run={'dataset_id':1,'source_state_id':2,'gold_run_id':3,'dataset_version_id':4,'policy_id':5,'source_sha256':'a'*64,'build_version':1}
    p={'load_strategy':'APPEND','schema_columns':[{'name':'id'},{'name':'amount'}],'business_keys':['id']}
    key,checksum,_=store.build(run,build_cumulative_gold(pd.DataFrame({'id':[1],'amount':[10]}),p))
    assert store.validate(run,key,checksum,1,['id','amount'])
    manifest=json.loads(objects[key]); artifact=manifest['artifacts'][0]
    artifact[field] = [] if field=='dependencies' else 'MART' if field=='artifact_type' else 999
    objects[key]=store.json(manifest)
    with pytest.raises(ValueError): store.validate(run,key,hashlib.sha256(objects[key]).hexdigest(),1,['id','amount'])


def test_first_refresh_catalog_counts_idempotency_archive(gold):
    assert status(gold)['freshness']=='STALE'
    first = refresh(gold)
    assert first['freshness']=='FRESH' and first['analytics_rows']==first['trusted_rows']==2
    objects = dict(gold[0][0][4])
    assert refresh(gold)==first and objects==gold[0][0][4]
    fixture,store=gold; pipe=fixture[0]; db=pipe[0]
    catalog = service.current_catalog(db[3])
    assert catalog['source_state_id']==first['current_state_id'] and catalog['base_artifact']['row_count']==2
    assert store.frame(catalog['base_artifact']['storage_path'],catalog['base_artifact']['sha256']).id.tolist()==[1,3]
    archive_delivery(pipe[2],db[1],db[2],db[3])
    assert refresh(gold)==first
    assert not any(v in str(first) for v in ['storage_path','s3://','manifest','silver/','gold/'])


def test_new_append_state_stale_rebuild_and_old_gold_preserved(gold):
    first=refresh(gold); fixture,store=gold; pipe=fixture[0]; db=pipe[0]
    old=dict(pipe[4]); apply(fixture,second_delivery(pipe))
    stale=status(gold)
    assert stale['freshness']=='STALE' and stale['trusted_rows']==4 and stale['gold_run_id']==first['gold_run_id']
    assert not service.current_catalog(db[3])['analytics_ready']
    later=refresh(gold)
    assert later['analytics_rows']==4 and later['gold_run_id']!=first['gold_run_id']
    assert all(pipe[4][k]==v for k,v in old.items())


@pytest.mark.parametrize('boundary',['build','validate','publish'])
def test_failure_previous_pointer_retry_atomicity(gold,boundary):
    first=refresh(gold); fixture,store=gold; pipe=fixture[0]
    apply(fixture,second_delivery(pipe)); patch=fixture[2]
    target=service if boundary!='validate' else store
    name={'build':'build_cumulative_gold','validate':'validate','publish':'publish_gold'}[boundary]
    original=getattr(target,name)
    def fail(*args,**kwargs):
        if boundary=='publish': original(*args,**kwargs)  # rollback after pointer writes
        raise ValueError('synthetic private details')
    patch.setattr(target,name,fail)
    with pytest.raises(RuntimeError): refresh(gold)
    failed=status(gold)
    assert failed['freshness']=='FAILED' and failed['gold_run_id']==first['gold_run_id'] and failed['trusted_rows']==4
    patch.setattr(target,name,original)
    assert refresh(gold)['analytics_rows']==4
    db=pipe[0]
    with db[0]() as conn:
        assert conn.execute('SELECT COUNT(*) FROM dataset_gold_runs').fetchone()[0]==2


def test_state_changes_during_refresh_stays_stale(gold):
    fixture,store=gold; pipe=fixture[0]; patch=fixture[2]
    original=store.build
    def race(*args):
        result=original(*args)
        apply(fixture,second_delivery(pipe))
        return result
    patch.setattr(store,'build',race)
    result=refresh(gold)
    assert result['freshness']=='STALE' and not result['analytics_ready'] and result['gold_run_id'] is None
    db=pipe[0]
    with db[0]() as conn:
        assert conn.execute("SELECT status FROM dataset_gold_runs").fetchone()[0]=='SUCCESS'
    patch.setattr(store,'build',original)
    assert refresh(gold)['analytics_rows']==4


def test_concurrent_refresh_ownership_and_readiness(gold):
    fixture,store=gold; db=fixture[0][0]; patch=fixture[2]
    entered=Event(); release=Event(); original=store.build
    def pause(*args):
        entered.set(); assert release.wait(20); return original(*args)
    patch.setattr(store,'build',pause)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future=executor.submit(refresh,gold)
        assert entered.wait(20)
        assert status(gold)['freshness']=='REFRESHING'
        with pytest.raises(RuntimeError): refresh(gold)
        release.set(); assert future.result()['freshness']=='FRESH'
    with pytest.raises(PermissionError): service.refresh_analytics(db[2]+1,db[3],db[1])
    with pytest.raises(PermissionError): service.readiness(db[2],db[3]+1,db[1])


def test_artifact_checksum_catalog_validation(gold):
    fixture,store=gold; refresh(gold); db=fixture[0][0]
    from psycopg.rows import dict_row
    with db[0]() as conn:
        run=conn.cursor(row_factory=dict_row).execute('SELECT * FROM dataset_gold_runs').fetchone()
    artifact=run['catalog'][0]
    objects=fixture[0][4]; body=objects[artifact['storage_path']]
    objects[artifact['storage_path']]=b'corrupt'
    with pytest.raises(ValueError): store.validate(run,run['manifest_key'],run['manifest_sha256'],2,['amount','id','status'])
    objects[artifact['storage_path']]=body


def test_api_scope_not_ready_and_safe_response(gold):
    from app.main import app
    from app.api.identity import get_current_user
    from fastapi.testclient import TestClient
    db=gold[0][0][0]; app.dependency_overrides[get_current_user]=lambda:db[1]
    client=TestClient(app); url=f'/workspaces/{db[2]}/datasets/{db[3]}/analytics'
    try:
        assert client.get(url+'/readiness').json()['freshness']=='STALE'
        legacy=client.get(f'/analytics/dataset-versions/{db[5]}/workspace')
        assert legacy.status_code==200 and not legacy.json()['analytics_ready']
        assert client.get('/analytics/dataset-versions/999999/workspace').status_code==404
        assert client.post(url+'/ask',json={'question':'How many records?'}).status_code==409
        assert client.get(f'/workspaces/{db[2]+1}/datasets/{db[3]}/analytics/readiness').status_code==403
        result=client.post(url+'/refresh')
        assert result.status_code==200 and result.json()['analytics_ready']
        assert 'storage_path' not in result.text
    finally:
        app.dependency_overrides.pop(get_current_user,None)


def test_dashboard_current_counts_and_io_race(gold):
    from app.services import analytics_service
    fixture,store=gold; db=fixture[0][0]; patch=fixture[2]
    refresh(gold)
    patch.setattr(analytics_service,'_read_artifact',lambda a:store.frame(a['storage_path'],a['sha256']))
    dashboard=service.read_dashboard(db[2],db[3],db[1])
    assert dashboard['analytics_ready'] and any(k['value']==2 and k['aggregation']=='COUNT' for k in dashboard['kpis'])
    original=analytics_service._read_artifact
    changed=False
    def race(a):
        nonlocal changed
        frame=original(a)
        if not changed:
            changed=True; apply(fixture,second_delivery(fixture[0]))
        return frame
    patch.setattr(analytics_service,'_read_artifact',race)
    dashboard=service.read_dashboard(db[2],db[3],db[1])
    assert not dashboard['analytics_ready'] and not dashboard['kpis'] and dashboard['trusted_rows']==4


def test_interrupted_run_recoverable_and_success_immutable(gold):
    fixture,_=gold; db=fixture[0][0]
    from psycopg.rows import dict_row
    # Simulate durable REFRESHING metadata after the coordinating process exited.
    with db[0]() as conn:
        s=conn.cursor(row_factory=dict_row).execute('SELECT s.* FROM datasets d JOIN dataset_state_versions s ON s.state_id=d.current_state_id WHERE d.dataset_id=%s',(db[3],)).fetchone()
        conn.execute("INSERT INTO dataset_gold_runs(dataset_id,source_state_id,dataset_version_id,policy_id,source_sha256,status) VALUES (%s,%s,%s,%s,%s,'REFRESHING')",(db[3],s['state_id'],s['dataset_version_id'],s['policy_id'],s['manifest_sha256']))
        conn.execute("UPDATE datasets SET state_analytics_status='REFRESHING' WHERE dataset_id=%s",(db[3],))
    assert status(gold)['failure_code']=='INTERRUPTED' and status(gold)['can_refresh']
    assert refresh(gold)['freshness']=='FRESH'
    import psycopg
    with db[0]() as conn, pytest.raises(psycopg.Error):
        conn.execute("UPDATE dataset_gold_runs SET row_count=999")
