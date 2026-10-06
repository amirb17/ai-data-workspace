"""UPSERT semantics plus real PostgreSQL publication/concurrency and immutable Parquet."""
import io
import json
from datetime import datetime,timezone
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import pytest
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline,approve_first,execute
from tests.test_append_execution import append_pipeline
from tests.test_incremental_foundation import policy,second_delivery
from app.processing.upsert_engine import upsert_rows,UPSERT_LINEAGE,state_index,validate_upsert_transition
from app.services import append_application_service as execution
from app.services.incremental_policy_service import read_foundation,IncrementalConflict
from app.services.processing_context_service import read_processing_context
from app.services.delivery_lifecycle_service import archive_delivery
from app.storage.incremental_artifacts import IncrementalArtifacts


def pure_policy(keys=None,event=None):
    return {'load_strategy':'UPSERT','normalization_version':1,'business_keys':keys or ['customer_id'],
            'schema_columns':[{'name':c} for c in ['customer_id','city']+(['updated_at'] if event else [])], 'event_time_column':event}


def pure_apply(current,rows,p=None,number=1):
    p=p or pure_policy()
    incoming=pd.DataFrame(rows,columns=[c['name'] for c in p['schema_columns']])
    app={'upload_request_id':number,'application_id':number,'policy_id':1,'applied_rule_version':1,
         'started_at':datetime(2026,10,6,12,number,tzinfo=timezone.utc),'valid_rows':len(incoming)}
    if current is None: current=pd.DataFrame(columns=list(incoming.columns)+UPSERT_LINEAGE)
    return upsert_rows(current,incoming,p,app)


def test_first_next_unchanged_update_insert_and_original_lineage():
    first,counts,_=pure_apply(None,[['C1','Pune'],['C2','Mumbai'],['C3','Delhi']])
    assert counts['inserted_rows']==3
    result,counts,ledger=pure_apply(first,[['C1','Pune'],['C2','Bangalore'],['C4','Chennai']],number=2)
    assert (counts['inserted_rows'],counts['updated_rows'],counts['unchanged_rows'],len(result))==(1,1,1,4)
    assert result.city.tolist()==['Pune','Bangalore','Delhi','Chennai']
    assert result._datarise_origin_upload_id.tolist()==[1,1,1,2]
    assert result._datarise_upload_id.tolist()==[1,2,1,2]
    assert result.iloc[0].to_dict()==first.iloc[0].to_dict()
    assert {r['outcome'] for r in ledger}=={'INSERTED','UPDATED','UNCHANGED'}


@pytest.mark.parametrize('rows,expected',[
    ([['C1','Pune'],['C1','Pune']],(1,1,0)),
    ([['C1','Pune'],['C1','Mumbai']],(0,0,2)),
    ([[None,'Pune']],(0,0,1)),
])
def test_incoming_duplicates_conflicts_nulls(rows,expected):
    result,counts,_=pure_apply(None,rows)
    assert (counts['inserted_rows'],counts['duplicate_rows'],counts['incremental_rejected_rows'])==expected
    assert len(result)==expected[0]


def test_composite_keys_order_and_no_key_rejected():
    p=pure_policy(keys=['customer_id','city'])
    result,counts,_=pure_apply(None,[['C1','Pune'],['C1','Mumbai']],p)
    assert counts['inserted_rows']==2 and len(state_index(result,p))==2
    p['business_keys']=[]
    with pytest.raises(ValueError): pure_apply(None,[['C1','Pune']],p)


@pytest.mark.parametrize('time,city,kind',[
    ('2026-10-06T00:00:00Z','Bangalore','UPDATED'),
    ('2026-09-01T00:00:00Z','Mumbai','STALE'),
    ('2026-10-05T00:00:00Z','Pune','UNCHANGED'),
    ('2026-10-05T00:00:00Z','Mumbai','REJECTED'),
])
def test_event_ordering(time,city,kind):
    p=pure_policy(event='updated_at')
    first,_,_=pure_apply(None,[['C1','Pune',pd.Timestamp('2026-10-05T00:00:00Z')]],p)
    result,counts,ledger=pure_apply(first,[['C1',city,pd.Timestamp(time)]],p,number=2)
    assert ledger[0]['outcome']==kind
    assert result.iloc[0].city==('Bangalore' if kind=='UPDATED' else 'Pune')
    assert sum(v for k,v in counts.items() if k!='conflict_rows')==1


def test_ambiguous_timestamp_and_duplicate_trusted_key_blocked():
    p=pure_policy(event='updated_at')
    result,counts,_=pure_apply(None,[['C1','Pune',pd.Timestamp('2026-10-05')]],p)
    assert counts['incremental_rejected_rows']==1 and result.empty
    first,_,_=pure_apply(None,[['C1','Pune']])
    with pytest.raises(ValueError,match='duplicate'): pure_apply(pd.concat([first,first]),[['C1','Mumbai']])


def test_transition_rejects_lost_original_lineage_and_retained_record_mutation():
    p=pure_policy()
    first,_,_=pure_apply(None,[['C1','Pune'],['C2','Mumbai']])
    result,_,ledger=pure_apply(first,[['C1','Bangalore']],number=2)
    result.loc[result.customer_id=='C1','_datarise_origin_upload_id']=999
    app={'started_at':datetime(2026,10,6,12,2,tzinfo=timezone.utc)}
    with pytest.raises(ValueError,match='original insertion'): validate_upsert_transition(state_index(first,p),state_index(result,p),ledger,app)


@pytest.fixture
def upsert_pipeline(append_pipeline):
    pipe,_,patch=append_pipeline
    p=policy(pipe,load_strategy='UPSERT',business_keys=['id'],expected_policy_version=1,confirm_policy_change=True)
    return pipe,p,patch


def apply(fixture,upload=None):
    pipe,_,_=fixture; db=pipe[0]
    return execution.apply_incremental(db[2],db[3],db[1],upload or pipe[2])


def test_pg_first_same_physical_new_delivery_and_retry(upsert_pipeline):
    first=apply(upsert_pipeline); pipe,p,_=upsert_pipeline
    assert (first['inserted_rows'],first['updated_rows'],first['unchanged_rows'])==(2,0,0)
    later=apply(upsert_pipeline,second_delivery(pipe))
    assert later['application_id']!=first['application_id'] and later['source_dq_run_id']==first['source_dq_run_id']
    assert (later['inserted_rows'],later['updated_rows'],later['unchanged_rows'],later['current_state_rows'])==(0,0,2,2)
    assert later['deactivated_rows'] is None
    before=set(pipe[4]); assert apply(upsert_pipeline,later['upload_request_id'])==later and before==set(pipe[4])
    with pytest.raises(IncrementalConflict): policy(pipe,load_strategy='APPEND',business_keys=['id'],expected_policy_version=2,confirm_policy_change=True)
    with pytest.raises(IncrementalConflict): policy(pipe,business_keys=['status'],expected_policy_version=2,confirm_policy_change=True)
    context=read_processing_context(later['upload_request_id'],pipe[0][1])
    assert context['updated_rows']==0 and context['unchanged_rows']==2 and context['load_policy']['business_keys']==['id']
    assert context['state_lineage']=={'source_state_version':1,'result_state_version':2}
    assert not any(s in str(context) for s in ('s3://','manifest_key','silver_path'))


@pytest.mark.parametrize('boundary',['manifest','head','engine','validation','storage','publication','response'])
def test_pg_failure_recovery_and_committed_response(upsert_pipeline,boundary):
    first=apply(upsert_pipeline); pipe,p,patch=upsert_pipeline
    second=second_delivery(pipe); before=dict(pipe[5])
    target=execution; method={'manifest':'delivery_manifest','head':'current_state','engine':'upsert_rows','publication':'_publish_metadata'}.get(boundary)
    if boundary=='response': target=execution.logger; method='info'
    if boundary in ('validation','storage'): target=IncrementalArtifacts; method='validate_state' if boundary=='validation' else 'put'
    original=getattr(target,method)
    def fail(*args,**kwargs):
        if boundary=='validation' and args[3]['upload_request_id']!=second: return original(*args,**kwargs)
        raise RuntimeError('private/synthetic-secret')
    patch.setattr(target,method,fail)
    with pytest.raises(RuntimeError): apply(upsert_pipeline,second)
    head=read_foundation(pipe[0][2],pipe[0][3],pipe[0][1])['current_state']
    assert head['state_id']!=first['result_state_id'] if boundary=='response' else head['state_id']==first['result_state_id']
    patch.setattr(target,method,original)
    assert apply(upsert_pipeline,second)['current_state_rows']==2 and pipe[5]==before


@pytest.mark.parametrize('same_key',[False,True])
def test_pg_concurrent_updates_reload_latest_head(upsert_pipeline,same_key):
    first=apply(upsert_pipeline); pipe,p,patch=upsert_pipeline
    a,b=second_delivery(pipe),second_delivery(pipe)
    entered,release=Event(),Event(); original=execution.upsert_rows
    def updated(current,incoming,policy,app):
        incoming=incoming.copy()
        for index,row in incoming.iterrows():
            incoming.at[index,'status']=current.loc[current.id==row.id,'status'].iloc[0]
        if app['upload_request_id']==a: incoming.loc[incoming.id==1,'status']='Mumbai'
        if app['upload_request_id']==b: incoming.loc[incoming.id==(1 if same_key else 3),'status']='Delhi'
        if app['upload_request_id']==a: entered.set(); assert release.wait(15)
        return original(current,incoming,policy,app)
    patch.setattr(execution,'upsert_rows',updated)
    with ThreadPoolExecutor(2) as pool:
        pending=pool.submit(apply,upsert_pipeline,a); assert entered.wait(10)
        try:
            with pytest.raises(RuntimeError,match='already active'): apply(upsert_pipeline,b)
        finally: release.set()
        assert pending.result()['updated_rows']==1
    final=apply(upsert_pipeline,b)
    _,frame=execution.current_state(execution.IncrementalArtifacts(),final,p)
    assert frame.loc[frame.id==1,'status'].iloc[0]==('Delhi' if same_key else 'Mumbai')
    if not same_key: assert frame.loc[frame.id==3,'status'].iloc[0]=='Delhi'


def test_pg_archive_scope_and_schema(upsert_pipeline):
    first=apply(upsert_pipeline); pipe,_,_=upsert_pipeline; db=pipe[0]
    archive_delivery(pipe[2],db[1],db[2],db[3]); assert apply(upsert_pipeline)==first
    other=second_delivery(pipe); archive_delivery(other,db[1],db[2],db[3])
    with pytest.raises(RuntimeError): apply(upsert_pipeline,other)
    with pytest.raises(PermissionError): execution.apply_incremental(db[2],db[3],{'user_id':999,'owner_key':'dev:other'},pipe[2])
    with pytest.raises(PermissionError): execution.apply_incremental(db[2]+1,db[3],db[1],pipe[2])
    changed,_=pipe[1]('id,name\ntext,Alice\n')
    with pytest.raises(ValueError): apply(upsert_pipeline,changed)
