"""SNAPSHOT lifecycle, conservative time ordering and shared atomic publication."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import psycopg
import pandas as pd
import pytest
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline, approve_first, execute
from tests.test_append_execution import append_pipeline
from tests.test_incremental_foundation import policy, second_delivery
from app.processing.snapshot_engine import snapshot_rows, SNAPSHOT_LINEAGE, validate_snapshot_transition
from app.services import append_application_service as execution
from app.services.incremental_policy_service import read_foundation, IncrementalConflict
from app.services.snapshot_context_service import declare_snapshot
from app.schemas.incremental import SnapshotDeliveryRequest, LoadPolicyRequest
from app.storage.incremental_artifacts import IncrementalArtifacts
from app.services.processing_context_service import read_processing_context
from app.services.delivery_lifecycle_service import archive_delivery


def pure(rows,current=None,number=1,coverage='COMPLETE',effective=None,boundary=None,kind='NORMAL',event=False,rejected=0):
    columns=['customer_id','city']+(['updated_at'] if event else [])
    incoming=pd.DataFrame(rows,columns=columns)
    p={'load_strategy':'SNAPSHOT','normalization_version':1,'business_keys':['customer_id'],
       'schema_columns':[{'name':c} for c in columns],'event_time_column':'updated_at' if event else None,'snapshot_coverage':'COMPLETE'}
    app={'upload_request_id':number,'application_id':number,'policy_id':1,'applied_rule_version':1,
         'started_at':datetime(2026,10,7,12,number,tzinfo=timezone.utc),'valid_rows':len(rows),'rejected_rows':rejected,
         'snapshot_coverage':coverage,'snapshot_effective_at':effective,'previous_snapshot_boundary_at':boundary,'delivery_kind':kind}
    if current is None: current=pd.DataFrame(columns=columns+SNAPSHOT_LINEAGE)
    frame,counts,ledger=snapshot_rows(current,incoming,p,app)
    return frame,counts,ledger,p,app


def test_complete_lifecycle_partial_and_reactivation_origin():
    first,c,*_=pure([['C1','Pune'],['C2','Mumbai'],['C3','Delhi']])
    assert c['inserted_rows']==c['active_rows']==3
    second,c,*_=pure([['C1','Pune'],['C3','Bangalore'],['C4','Chennai']],first,2)
    assert (c['inserted_rows'],c['updated_rows'],c['unchanged_rows'],c['deactivated_rows'],c['active_rows'],c['inactive_rows'])==(1,1,1,1,3,1)
    third,c,*_=pure([['C1','Pune'],['C2','Mumbai'],['C3','Bangalore'],['C4','Chennai']],second,3)
    assert c['reactivated_rows']==1 and c['updated_rows']==0 and c['active_rows']==4
    row=third.loc[third.customer_id=='C2'].iloc[0]
    assert row._datarise_origin_upload_id==1 and row._datarise_deactivated_upload_id==2 and row._datarise_reactivated_upload_id==3
    fourth,c,*_=pure([['C1','Pune'],['C3','Bangalore']],third,4,coverage='PARTIAL')
    assert c['active_rows']==4 and c['deactivated_rows']==0
    assert first.city.tolist()==['Pune','Mumbai','Delhi']


def test_changed_reactivation_duplicates_conflicts_and_rejected_complete_safety():
    first,*_=pure([['C1','Pune'],['C2','Mumbai']])
    second,*_=pure([['C1','Pune']],first,2)
    third,c,*_=pure([['C2','Delhi'],['C2','Delhi']],second,3,coverage='PARTIAL')
    assert c['reactivated_rows']==1 and c['duplicate_rows']==1 and c['updated_rows']==0
    assert third.loc[third.customer_id=='C2','city'].iloc[0]=='Delhi'
    fourth,c,_,_,app=pure([['C1','Mumbai'],['C1','Delhi']],first,4)
    assert c['conflict_rows']==2 and c['deactivated_rows']==0 and app['snapshot_outcome']=='DEACTIVATION_WITHHELD'
    assert fourth.city.tolist()==first.city.tolist()
    _,c,*_=pure([['C1','Pune']],first,5,rejected=1)
    assert c['deactivated_rows']==0


def test_late_equal_correction_backfill_and_newer_boundary():
    newer='2026-10-06T00:00:00Z'; older='2026-09-01T00:00:00Z'
    first,*_=pure([['C1','Pune'],['C2','Mumbai']],effective=newer)
    result,c,_,_,app=pure([['C1','Delhi'],['C3','Chennai']],first,2,effective=older,boundary=newer,kind='BACKFILL')
    assert c['stale_rows']==2 and c['deactivated_rows']==0 and c['inserted_rows']==0 and app['snapshot_outcome']=='STALE'
    assert result.city.tolist()==first.city.tolist()
    _,c,*_=pure([['C1','Pune'],['C2','Mumbai']],first,3,effective=newer,boundary=newer)
    assert c['unchanged_rows']==2
    _,c,*_=pure([['C1','Delhi']],first,4,effective=newer,boundary=newer)
    assert c['conflict_rows']==1 and c['deactivated_rows']==0
    correction,c,*_=pure([['C1','Delhi']],first,5,effective=newer,boundary=newer,kind='CORRECTION')
    assert c['updated_rows']==1 and c['deactivated_rows']==0 and c['active_rows']==2
    _,c,*_=pure([['C1','Chennai']],correction,6,effective='2026-10-07T00:00:00Z',boundary=newer)
    assert c['updated_rows']==1 and c['deactivated_rows']==1


def test_event_ordering_and_inactive_stale_reappearance():
    t=pd.Timestamp('2026-10-06T00:00:00Z')
    first,*_=pure([['C1','Pune',t],['C2','Mumbai',t]],event=True,effective=t)
    second,*_=pure([['C1','Pune',t]],first,2,event=True,effective='2026-10-07T00:00:00Z',boundary=t)
    result,c,*_=pure([['C2','Delhi',t-pd.Timedelta(days=1)]],second,3,event=True,effective='2026-10-08T00:00:00Z',boundary='2026-10-07T00:00:00Z',coverage='PARTIAL')
    assert c['stale_rows']==1 and c['reactivated_rows']==0 and c['inactive_rows']==1
    result,c,*_=pure([['C2','Delhi',t+pd.Timedelta(days=2)]],second,4,event=True,effective='2026-10-08T00:00:00Z',boundary='2026-10-07T00:00:00Z',coverage='PARTIAL')
    assert c['reactivated_rows']==1 and c['active_rows']==2


def test_explicit_context_and_candidate_tamper():
    with pytest.raises(ValueError): SnapshotDeliveryRequest(coverage='COMPLETE',effective_at='2026-10-06T00:00:00')
    with pytest.raises(ValueError): LoadPolicyRequest(dataset_version_id=1,expected_policy_version=0,load_strategy='SNAPSHOT',business_keys=['id'],schema_evolution_policy='STRICT')
    first,*_=pure([['C1','Pune'],['C2','Mumbai']])
    frame,c,ledger,p,app=pure([['C1','Pune']],first,2)
    frame.loc[frame.customer_id=='C2','_datarise_origin_upload_id']=999
    with pytest.raises(ValueError,match='original lineage'): validate_snapshot_transition(first,frame,p,app,c,ledger)


@pytest.fixture
def snapshot_pipeline(append_pipeline):
    pipe,_,patch=append_pipeline
    p=policy(pipe,load_strategy='SNAPSHOT',business_keys=['id'],snapshot_coverage='COMPLETE',expected_policy_version=1,confirm_policy_change=True)
    # Existing fixture has one quarantined row; snapshot will conservatively withhold absence deactivation.
    db=pipe[0]
    declare_snapshot(db[2],db[3],db[1],pipe[2],SnapshotDeliveryRequest(coverage='COMPLETE',effective_at='2026-10-06T00:00:00Z'))
    return pipe,p,patch


def apply(fixture,upload=None):
    pipe,_,_=fixture; db=pipe[0]
    return execution.apply_incremental(db[2],db[3],db[1],upload or pipe[2])


def next_upload(fixture,effective='2026-10-07T00:00:00Z',coverage='COMPLETE',kind='NORMAL'):
    pipe,_,_=fixture; db=pipe[0]; upload=second_delivery(pipe)
    declare_snapshot(db[2],db[3],db[1],upload,SnapshotDeliveryRequest(coverage=coverage,effective_at=effective,delivery_kind=kind))
    return upload


def test_pg_publication_retry_history_metrics_and_policy_lock(snapshot_pipeline):
    first=apply(snapshot_pipeline); pipe,p,_=snapshot_pipeline; db=pipe[0]
    assert (first['inserted_rows'],first['active_rows'],first['inactive_rows'])==(2,2,0)
    before=dict(pipe[4]); assert apply(snapshot_pipeline)==first and pipe[4]==before
    later=apply(snapshot_pipeline,next_upload(snapshot_pipeline))
    assert later['application_id']!=first['application_id'] and later['unchanged_rows']==2
    assert all(pipe[4][k]==v for k,v in before.items())
    context=read_processing_context(later['upload_request_id'],db[1],db[2],db[3])
    assert context['active_rows']==2 and context['snapshot_context']['coverage']=='COMPLETE'
    assert not any(s in str(context) for s in ('s3://','manifest_key','silver_path'))
    head=read_foundation(db[2],db[3],db[1])['current_state']
    assert head['active_rows']==2 and head['state_analytics_status']=='STALE'
    with pytest.raises(IncrementalConflict): policy(pipe,load_strategy='SNAPSHOT',snapshot_coverage='PARTIAL',expected_policy_version=2,confirm_policy_change=True)
    with pytest.raises(IncrementalConflict): policy(pipe,load_strategy='SNAPSHOT',snapshot_coverage='COMPLETE',business_keys=['status'],expected_policy_version=2,confirm_policy_change=True)
    with pytest.raises(IncrementalConflict): declare_snapshot(db[2],db[3],db[1],pipe[2],SnapshotDeliveryRequest(coverage='PARTIAL'))
    archive_delivery(pipe[2],db[1],db[2],db[3]); assert apply(snapshot_pipeline)==first
    blocked=next_upload(snapshot_pipeline); archive_delivery(blocked,db[1],db[2],db[3])
    with pytest.raises(RuntimeError): apply(snapshot_pipeline,blocked)
    with pytest.raises(PermissionError): execution.apply_incremental(db[2]+1,db[3],db[1],pipe[2])


@pytest.mark.parametrize('boundary',['engine','validation','publication','response'])
def test_pg_rollback_recovery_and_response_loss(snapshot_pipeline,boundary):
    first=apply(snapshot_pipeline); pipe,p,patch=snapshot_pipeline; db=pipe[0]
    second=next_upload(snapshot_pipeline)
    target=execution.logger if boundary=='response' else IncrementalArtifacts if boundary=='validation' else execution
    method={'engine':'snapshot_rows','validation':'validate_state','publication':'_publish_metadata','response':'info'}[boundary]
    original=getattr(target,method)
    def fail(*args,**kwargs):
        if boundary=='validation' and args[3]['upload_request_id']!=second: return original(*args,**kwargs)
        raise RuntimeError('synthetic/private')
    patch.setattr(target,method,fail)
    with pytest.raises(RuntimeError): apply(snapshot_pipeline,second)
    head=read_foundation(db[2],db[3],db[1])['current_state']
    assert (head['state_id']!=first['result_state_id']) == (boundary=='response')
    patch.setattr(target,method,original)
    assert apply(snapshot_pipeline,second)['active_rows']==2


def test_pg_late_snapshot_preserves_state_and_no_deactivation(snapshot_pipeline):
    first=apply(snapshot_pipeline)
    later=apply(snapshot_pipeline,next_upload(snapshot_pipeline,effective='2026-09-01T00:00:00Z',kind='BACKFILL'))
    assert later['stale_rows']==2 and later['deactivated_rows']==0 and later['snapshot_outcome']=='STALE'
    assert later['snapshot_boundary_at']==first['snapshot_boundary_at']


def test_pg_requires_declaration_and_partial_downgrade(snapshot_pipeline):
    pipe,_,_=snapshot_pipeline
    with pytest.raises(ValueError): apply(snapshot_pipeline,second_delivery(pipe))
    result=apply(snapshot_pipeline,next_upload(snapshot_pipeline,coverage='PARTIAL'))
    assert result['snapshot_coverage']=='PARTIAL' and result['deactivated_rows']==0


@pytest.mark.parametrize('newer_first',[True,False])
def test_pg_concurrent_effective_times_serialize_and_recheck_head(snapshot_pipeline,newer_first):
    pipe,p,patch=snapshot_pipeline; db=pipe[0]
    first=apply(snapshot_pipeline)
    older=next_upload(snapshot_pipeline,effective='2026-10-07T00:00:00Z')
    newer=next_upload(snapshot_pipeline,effective='2026-10-08T00:00:00Z')
    a,b=(newer,older) if newer_first else (older,newer)
    entered,release=Event(),Event(); original=execution.snapshot_rows
    def slow(*args):
        if args[3]['upload_request_id']==a: entered.set(); assert release.wait(15)
        return original(*args)
    patch.setattr(execution,'snapshot_rows',slow)
    with ThreadPoolExecutor(2) as pool:
        running=pool.submit(apply,snapshot_pipeline,a); assert entered.wait(10)
        try:
            with pytest.raises(RuntimeError,match='already active'): apply(snapshot_pipeline,b)
        finally: release.set()
        running.result()
    last=apply(snapshot_pipeline,b)
    assert last['snapshot_boundary_at']==datetime(2026,10,8,tzinfo=timezone.utc)
    if newer_first: assert last['snapshot_outcome']=='STALE' and last['stale_rows']==2
    assert first['snapshot_boundary_at']==datetime(2026,10,6,tzinfo=timezone.utc)


def test_pg_publication_write_then_rollback_and_immutable_declarations(snapshot_pipeline):
    pipe,p,patch=snapshot_pipeline; db=pipe[0]
    first=apply(snapshot_pipeline); upload=next_upload(snapshot_pipeline)
    original=execution._publish_metadata
    def fail_after_writes(*args):
        original(*args)
        raise RuntimeError('transaction rollback')
    patch.setattr(execution,'_publish_metadata',fail_after_writes)
    with pytest.raises(RuntimeError): apply(snapshot_pipeline,upload)
    assert read_foundation(db[2],db[3],db[1])['current_state']['state_id']==first['result_state_id']
    patch.setattr(execution,'_publish_metadata',original)
    assert apply(snapshot_pipeline,upload)['active_rows']==2
    for sql in ["UPDATE snapshot_delivery_contexts SET coverage='PARTIAL'", "UPDATE delivery_applications SET snapshot_coverage='PARTIAL'", "UPDATE dataset_state_versions SET row_count=99 WHERE status='PUBLISHED'"]:
        with db[0]() as conn:
            with pytest.raises(psycopg.DatabaseError): conn.execute(sql)


@pytest.mark.parametrize('coverage',['COMPLETE','PARTIAL'])
def test_empty_explicit_snapshot_and_unknown_identity_withhold(coverage):
    first,*_=pure([['C1','Pune'],['C2','Mumbai']])
    empty,c,*_=pure([],first,2,coverage=coverage)
    assert len(empty)==2 and c['deactivated_rows']==(2 if coverage=='COMPLETE' else 0)
    result,c,*_=pure([[None,'Delhi']],first,3)
    assert c['incremental_rejected_rows']==1 and c['deactivated_rows']==0 and c['active_rows']==2


def test_future_record_time_and_missing_effective_time_blocked():
    _,c,*_=pure([['C1','Pune',pd.Timestamp('2026-10-08T00:00:00Z')]],event=True,effective='2026-10-07T00:00:00Z')
    assert c['incremental_rejected_rows']==1 and c['inserted_rows']==0
    with pytest.raises(ValueError,match='effective time'):
        pure([['C1','Pune',pd.Timestamp('2026-10-08T00:00:00Z')]],event=True)


def test_pg_api_declaration_scope_and_no_private_paths(snapshot_pipeline):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.identity import get_current_user
    pipe,_,_=snapshot_pipeline; db=pipe[0]
    app.dependency_overrides[get_current_user]=lambda:db[1]
    try:
        with TestClient(app) as client:
            base=f'/workspaces/{db[2]}/datasets/{db[3]}/incremental'
            assert client.get(base).status_code==200
            body={'coverage':'COMPLETE','effective_at':'2026-10-06T00:00:00Z'}
            response=client.post(base+f'/deliveries/{pipe[2]}/snapshot-context',json=body)
            assert response.status_code==200 and response.json()['dataset_id']==db[3]
            assert not any(s in response.text for s in ('s3://','manifest_key','storage_path'))
            assert client.post(base.replace(f'/workspaces/{db[2]}',f'/workspaces/{db[2]+1}')+f'/deliveries/{pipe[2]}/snapshot-context',json=body).status_code==403
            assert client.post(base+f'/deliveries/{pipe[2]}/snapshot-context',json={'coverage':'COMPLETE','effective_at':'2026-10-06T00:00:00'}).status_code==422
    finally: app.dependency_overrides.clear()
