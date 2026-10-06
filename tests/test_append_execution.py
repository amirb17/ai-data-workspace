"""Real PostgreSQL and Parquet; in-memory S3 supports immutable conditional writes."""
import io
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pandas as pd
import psycopg
import pytest
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline,approve_first,execute
from tests.test_incremental_foundation import second_delivery,policy
from app.services import append_application_service as execution
from app.services.processing_context_service import read_processing_context
from app.services.delivery_lifecycle_service import archive_delivery
from app.storage.incremental_artifacts import IncrementalArtifacts
from app.processing.append_engine import append_rows,LINEAGE
from app.services.incremental_policy_service import read_foundation
from app.services import processing_service


@pytest.fixture
def append_pipeline(pipeline,monkeypatch):
    approve_first(pipeline); execute(pipeline)
    p=policy(pipeline,load_strategy='APPEND',business_keys=[])
    objects=pipeline[4]
    class Store:
        def get_object(self,Bucket,Key): return {'Body':io.BytesIO(objects[Key])}
        def put_object(self,Bucket,Key,Body,**kwargs):
            assert Key not in objects, 'immutable object overwritten'
            objects[Key]=Body
    monkeypatch.setattr(execution,'IncrementalArtifacts',lambda:IncrementalArtifacts(Store()))
    return pipeline,p,monkeypatch


def apply(fixture,upload=None):
    pipeline,_,_=fixture; db=pipeline[0]
    return execution.apply_append(db[2],db[3],db[1],upload or pipeline[2])


def state(fixture):
    pipeline,p,_=fixture; store=execution.IncrementalArtifacts()
    app=apply(fixture)
    _,frame=execution.current_state(store,app,p)
    return frame


def keyed(fixture):
    pipeline,p,monkeypatch=fixture
    newer=policy(pipeline,load_strategy='APPEND',business_keys=['id'],expected_policy_version=1,confirm_policy_change=True)
    return pipeline,newer,monkeypatch


def test_first_second_unkeyed_and_repeated_delivery_physical_reuse(append_pipeline):
    first=apply(append_pipeline)
    assert (first['inserted_rows'],first['duplicate_rows'],first['current_state_rows'])==(2,0,2)
    assert first['rejected_rows']==1 and first['updated_rows'] is None and first['deactivated_rows'] is None
    pipeline,_,_=append_pipeline
    second=second_delivery(pipeline); later=apply(append_pipeline,second)
    assert later['application_id']!=first['application_id'] and later['source_dq_run_id']==first['source_dq_run_id']
    assert later['current_state_rows']==4 and later['inserted_rows']==2
    frame=state(append_pipeline)
    assert frame['id'].tolist()==[1,3,1,3]
    assert frame['_datarise_upload_id'].tolist()==[pipeline[2],pipeline[2],second,second]
    before=set(pipeline[4])
    assert apply(append_pipeline,second)==later and before==set(pipeline[4])


def test_keyed_duplicates_separate_deliveries_not_updates(append_pipeline):
    fixture=keyed(append_pipeline)
    first=apply(fixture); second=apply(fixture,second_delivery(fixture[0]))
    assert first['inserted_rows']==2
    assert (second['inserted_rows'],second['duplicate_rows'],second['current_state_rows'])==(0,2,2)
    assert second['updated_rows'] is None and second['unchanged_rows'] is None


def test_engine_key_conflicts_identical_duplicates_and_lineage():
    p={'load_strategy':'APPEND','normalization_version':1,'business_keys':['id'],'schema_columns':[{'name':'id'},{'name':'value'}],'event_time_column':None}
    app={'upload_request_id':7,'application_id':8,'policy_id':9,'applied_rule_version':1}
    incoming=pd.DataFrame({'id':[1,1,2,2,3,None],'value':['a','a','x','y','new','bad']})
    empty=pd.DataFrame(columns=['id','value']+LINEAGE)
    frame,counts,rejected=append_rows(empty,incoming,p,app)
    assert frame['id'].tolist()==[1,3]
    assert counts=={'inserted_rows':2,'duplicate_rows':1,'incremental_rejected_rows':3}
    assert {r['reason'] for r in rejected}=={'CONFLICTING_INCOMING_KEY','INVALID_IDENTITY_OR_EVENT_TIME'}
    changed=pd.DataFrame({'id':[1,4],'value':['changed','new']})
    result,counts,_=append_rows(frame,changed,p,app)
    assert result['value'].tolist()==['a','new','new'] and counts['incremental_rejected_rows']==1
    assert result['_datarise_policy_id'].tolist()==[9,9,9]


def test_engine_late_event_time_no_timezone_guess_or_updates():
    p={'load_strategy':'APPEND','normalization_version':1,'business_keys':['id'],'schema_columns':[{'name':'id'},{'name':'event'}],'event_time_column':'event'}
    app={'upload_request_id':7,'application_id':8,'policy_id':9,'applied_rule_version':1}
    empty=pd.DataFrame(columns=['id','event']+LINEAGE)
    first=pd.DataFrame({'id':[1],'event':[pd.Timestamp('2026-10-06T12:00:00Z')]})
    current,_,_=append_rows(empty,first,p,app)
    incoming=pd.DataFrame({'id':[2,3,4],'event':[pd.Timestamp('2026-09-01T12:00:00Z'),pd.Timestamp('2026-09-01'),None]})
    result,counts,_=append_rows(current,incoming,p,app)
    assert result['id'].tolist()==[1,2] and counts['inserted_rows']==1 and counts['incremental_rejected_rows']==2
    assert result.iloc[0]['event']==current.iloc[0]['event']


@pytest.mark.parametrize('boundary',['manifest','head','engine','validation','storage','publication'])
def test_failure_preserves_head_and_retries_without_silver(append_pipeline,boundary):
    first=apply(append_pipeline); pipeline,_,monkeypatch=append_pipeline
    second=second_delivery(pipeline); before_calls=dict(pipeline[5])
    def fail(*args,**kwargs): raise RuntimeError('s3://private/row-secret')
    method={'manifest':'delivery_manifest','head':'current_state','engine':'append_rows','publication':'_publish_metadata'}.get(boundary)
    if method:
        original=getattr(execution,method); monkeypatch.setattr(execution,method,fail)
    else:
        method='validate_state' if boundary=='validation' else 'put'
        original=getattr(IncrementalArtifacts,method)
        def fail_candidate(store,*args,**kwargs):
            if method=='validate_state' and args[2]['upload_request_id']!=second:
                return original(store,*args,**kwargs)
            return fail()
        monkeypatch.setattr(IncrementalArtifacts,method,fail_candidate)
    with pytest.raises(RuntimeError,match='previous trusted state'): apply(append_pipeline,second)
    db=pipeline[0]
    current=read_foundation(db[2],db[3],db[1])
    assert current['current_state']['state_id']==first['result_state_id']
    context=read_processing_context(second,db[1],db[2],db[3])
    assert context['status']=='DATASET_UPDATE_FAILED' and context['stages']['silver']=='SUCCESS'
    assert 'private' not in str(context) and context['can_continue']
    monkeypatch.setattr(execution if boundary in ('manifest','head','engine','publication') else IncrementalArtifacts,method,original)
    recovered=apply(append_pipeline,second)
    assert recovered['current_state_rows']==4 and pipeline[5]==before_calls


def test_manifest_pinned_and_immutable_after_legacy_silver_changes(append_pipeline):
    pipeline,_,monkeypatch=append_pipeline
    original=execution.append_rows
    monkeypatch.setattr(execution,'append_rows',lambda *args:(_ for _ in ()).throw(RuntimeError('candidate failure')))
    with pytest.raises(RuntimeError): apply(append_pipeline)
    db=pipeline[0]
    with db[0]() as conn:
        source=conn.execute('SELECT silver_path FROM data_quality_runs ORDER BY dq_run_id DESC LIMIT 1').fetchone()[0]
        with pytest.raises(psycopg.DatabaseError): conn.execute('UPDATE delivery_manifests SET manifest_version=1')
    pipeline[4][execution.artifact_key(source)]=b'broken later rerun'
    monkeypatch.setattr(execution,'append_rows',original)
    assert apply(append_pipeline)['current_state_rows']==2


def test_response_loss_durable_success_and_retry(append_pipeline):
    _,_,monkeypatch=append_pipeline
    original=execution.logger.info
    monkeypatch.setattr(execution.logger,'info',lambda *args:(_ for _ in ()).throw(RuntimeError('response lost')))
    with pytest.raises(RuntimeError,match='completed; response interrupted'): apply(append_pipeline)
    monkeypatch.setattr(execution.logger,'info',original)
    pipeline=append_pipeline[0]; before=set(pipeline[4])
    assert apply(append_pipeline)['status']=='SUCCESS' and before==set(pipeline[4])


def test_distinct_delivery_concurrency_no_lost_update(append_pipeline):
    pipeline,_,monkeypatch=append_pipeline
    second=second_delivery(pipeline); entered,release=Event(),Event(); original=execution.append_rows
    def slow(*args):
        entered.set(); assert release.wait(15); return original(*args)
    monkeypatch.setattr(execution,'append_rows',slow)
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(apply,append_pipeline)
        assert entered.wait(10)
        try:
            with pytest.raises(RuntimeError,match='already active'): apply(append_pipeline,second)
        finally: release.set()
        assert first.result()['current_state_rows']==2
    monkeypatch.setattr(execution,'append_rows',original)
    assert apply(append_pipeline,second)['current_state_rows']==4


def test_archive_ownership_and_schema_safety(append_pipeline):
    first=apply(append_pipeline); pipeline,_,_=append_pipeline; db=pipeline[0]
    archive_delivery(pipeline[2],db[1],db[2],db[3])
    assert apply(append_pipeline)==first
    other=second_delivery(pipeline); archive_delivery(other,db[1],db[2],db[3])
    with pytest.raises(RuntimeError): apply(append_pipeline,other)
    with pytest.raises(PermissionError): execution.apply_append(db[2],db[3],{'user_id':999,'owner_key':'dev:other'},pipeline[2])
    changed,_=pipeline[1]('id,name\ntext,Alice\n')
    with pytest.raises(ValueError): apply(append_pipeline,changed)


def test_processing_lifecycle_before_gold_and_public_context(append_pipeline):
    pipeline,_,_=append_pipeline
    second=second_delivery(pipeline); db=pipeline[0]
    assert read_processing_context(second,db[1])['status']=='READY_TO_APPLY'
    from app.services.delivery_execution_service import continue_processing
    result=continue_processing(second,db[1],db[2],db[3])
    assert result['status']=='SUCCESS' and result['stages']['dataset_update']=='SUCCESS'
    assert result['inserted_rows']==2 and result['current_state_rows']==2
    assert not any(secret in str(result) for secret in ('s3://','manifest_key','silver_key','silver_path'))
