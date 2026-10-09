"""Exact aggregate evidence plus isolated PostgreSQL lineage/review/race checks."""
import copy
import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pandas as pd
import pytest
from app.processing.semantic_evidence import build_evidence
from app.processing.relationship_evidence import propose, verify, LIMITS, values
from app.services import relationship_service as service
from tests.test_semantic_profiles import pg,deliveries,pipeline,append_pipeline,profiled,refresh
from tests.test_append_execution import apply
from tests.test_incremental_foundation import second_delivery

def item(dataset,name,frame,keys):
    policy = {'load_strategy':'APPEND','business_keys':keys,'event_time_column':None,
              'normalization_version':1,'schema_columns':[{'name':c} for c in frame.columns]}
    summary,cols = build_evidence(frame,policy)
    return {'profile':{'dataset_name':name,'summary':summary,'columns':cols},'policy':policy,
            'pin':{'dataset_id':dataset,'dataset_version_id':dataset,'state_id':dataset,'state_version':1,'profile_id':dataset,'profile_version':1,'policy_id':dataset,'schema_version':1,'dataset_suggestion_id':None}}

def evidence(parent,child,pkeys=['customer_id'],ckeys=[]):
    p=item(1,'Customers',parent,pkeys); c=item(2,'Orders',child,ckeys)
    pairs,_ = propose([p,c])
    return verify(1,pairs,{1:parent,2:child})

def test_distinct_directional_overlap_nulls_repeat_rate_and_no_raw_values():
    result=evidence(pd.DataFrame({'customer_id':['secret-a','secret-b','secret-c']}),
                    pd.DataFrame({'customer_id':['secret-a','secret-a','missing-secret',None]}))[0]
    s=result['signals']; o=s['overlap']
    assert o['child_non_null_distinct_keys']==2 and o['matched_distinct_keys']==1 and o['missing_distinct_keys']==1
    assert o['child_to_parent_coverage']==.5 and o['parent_referenced_ratio']==pytest.approx(1/3)
    assert s['child']['duplicate_rows']==1 and s['child']['null_ratio']==.25
    assert result['candidate_cardinality']=='ONE_TO_MANY' and not result['can_confirm']
    assert sum(c['points'] for c in result['score_components'])==result['deterministic_score']
    assert 'secret-a' not in json.dumps(result) and 'missing-secret' not in json.dumps(result)

@pytest.mark.parametrize('mode',['one','many','repeated','empty','allnull'])
def test_cardinality_and_conservative_confirmation(mode):
    p=['a','b']; c=['a','b']
    if mode=='many': c=['a','a','b']
    if mode=='repeated': p=['a','a','b']; c=['a','a','b']
    if mode=='empty': c=[]
    if mode=='allnull': c=[None,None]
    # Explicit text dtype avoids guessing an empty reference column's type.
    result=evidence(pd.DataFrame({'customer_id':pd.Series(p,dtype='str')}),pd.DataFrame({'customer_id':pd.Series(c,dtype='str')}))[0]
    if mode in ('one','many'):
        assert result['can_confirm'] and result['candidate_cardinality']==('ONE_TO_ONE' if mode=='one' else 'ONE_TO_MANY')
    else: assert not result['can_confirm']
    if mode in ('empty','allnull'): assert result['signals']['overlap']['child_to_parent_coverage'] is None
    if mode=='repeated': assert result['candidate_cardinality']=='MANY_TO_MANY_CANDIDATE'

def test_type_mismatch_never_coerced_and_entity_qualified_id_matching():
    p=pd.DataFrame({'id':[1,2]}); c=pd.DataFrame({'customer_id':['1','2']})
    assert evidence(p,c,pkeys=['id'])==[]
    c=pd.DataFrame({'customer_id':[1,1,2]})
    r=evidence(p,c,pkeys=['id'])[0]
    assert r['signals']['name_features']==['ENTITY_QUALIFIED_IDENTIFIER'] and r['can_confirm']
    assert r['parent']['columns']==['id'] and r['child']['columns']==['customer_id']

def test_composite_components_suppressed_and_unrelated_pairs_pruned():
    p=pd.DataFrame({'order_id':['a','a'],'line_number':[1,2]})
    c=pd.DataFrame({'order_id':['a'],'region':['a']})
    pairs,suppressed=propose([item(1,'Orders',p,['order_id','line_number']),item(2,'Other',c,[])])
    assert all(pair[0]['pin']['dataset_id']!=1 for pair in pairs.values()) and suppressed==1
    assert not evidence(pd.DataFrame({'customer_id':['a']}),pd.DataFrame({'product_id':['a']}))

def test_no_case_whitespace_numeric_string_coercion_and_precision_guard():
    r=evidence(pd.DataFrame({'customer_id':['A','b']}),pd.DataFrame({'customer_id':['a',' b']}))[0]
    assert r['signals']['overlap']['matched_distinct_keys']==0 and not r['can_confirm']
    assert r['candidate_cardinality']=='UNKNOWN'
    with pytest.raises(ValueError,match='precision'): values(pd.DataFrame({'id':[float(2**53)]}),'id',[0])
    with pytest.raises(ValueError,match='length'): values(pd.DataFrame({'id':['x'*(LIMITS['value_characters']+1)]}),'id',[0])
    with pytest.raises(ValueError,match='budget'): values(pd.DataFrame({'id':['x']}),'id',[LIMITS['total_value_bytes']])

def test_limits_before_io_and_bounded_artifact_read():
    from app.storage.incremental_artifacts import IncrementalArtifacts
    class Client:
        def get_object(self,**kwargs): return {'Body':io.BytesIO(b'12345')}
    with pytest.raises(ValueError,match='byte limit'): IncrementalArtifacts(Client()).read('private',max_bytes=4)
    assert IncrementalArtifacts(Client()).read('private',max_bytes=5)==b'12345'
    source={'eligible':[{'profile':{'columns':[]},'state':{'row_count':100001}}]*2}
    assert '100000' in service.limitation(source)
    source['eligible']*=11
    assert '20' in service.limitation(source)

def test_snapshot_verification_uses_only_validated_active_records(monkeypatch):
    from contextlib import contextmanager
    class Connection:
        def cursor(self,**kwargs): return self
        def execute(self,*args): return self
        def fetchone(self): return {'application_id':1}
    @contextmanager
    def transaction(): yield Connection()
    frame=pd.DataFrame({'customer_id':['a','removed'],'_datarise_active':[True,False]})
    parent=item(1,'Customers',frame.loc[frame['_datarise_active'],['customer_id']],['customer_id'])
    parent['policy']['load_strategy']='SNAPSHOT'
    parent['state']={'source_application_id':1}
    child=item(2,'Orders',pd.DataFrame({'customer_id':['a']}),[])
    pairs,_=propose([parent,child])
    monkeypatch.setattr(service,'repository_transaction',transaction)
    monkeypatch.setattr(service,'BoundedArtifacts',lambda:object())
    monkeypatch.setattr(service,'load_pinned_state',lambda *_:(None,frame))
    # Only the pinned parent participates in this direct loader check.
    result=service.load_frames({'eligible':[parent]},pairs)
    assert result[1]['customer_id'].tolist()==['a']
    frame['_datarise_active']=pd.Series(['invalid',False],dtype=object)
    with pytest.raises(ValueError,match='activity'):service.load_frames({'eligible':[parent]},pairs)

def test_strict_evidence_rejects_fabricated_counts_scores_and_source_pins():
    from pydantic import ValidationError
    from app.schemas.relationships import RelationshipEvidence
    original=evidence(pd.DataFrame({'customer_id':['a']}),pd.DataFrame({'customer_id':['a']}))[0]
    for field in ('counts','score','source','confirmation'):
        broken=copy.deepcopy(original)
        if field=='counts':broken['signals']['overlap']['missing_distinct_keys']=1
        if field=='score':broken['deterministic_score']=99
        if field=='source':broken['source_pins'][1]['dataset_id']=100
        if field=='confirmation':broken['can_confirm']=False
        with pytest.raises(ValidationError):RelationshipEvidence.model_validate(broken)

@pytest.fixture
def relationship_ready(profiled):
    from tests.test_delivery_execution import approve_first,execute,CSV
    from tests.test_incremental_foundation import policy
    f=profiled; db=f[0][0]
    refresh(f)
    with db[0]() as conn:
        ds=conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES (%s,'Customers','dev:test') RETURNING dataset_id",(db[2],)).fetchone()[0]
    upload,result=f[0][1](CSV.replace('open,10','open,11'),target_dataset=ds)
    newdb=tuple(ds if i==3 else x for i,x in enumerate(db))
    newpipeline=(newdb,f[0][1],upload,result,f[0][4],f[0][5])
    approve_first(newpipeline);execute(newpipeline)
    p=policy(newpipeline,load_strategy='APPEND',business_keys=[])
    second=(newpipeline,p,f[2]);apply(second);refresh(second)
    f[2].setattr(service,'BoundedArtifacts',lambda:__import__('app.services.append_application_service',fromlist=['IncrementalArtifacts']).IncrementalArtifacts())
    return f,second

def test_real_discovery_review_history_staleness_isolation_and_api(relationship_ready):
    import psycopg
    from fastapi import HTTPException
    f,second=relationship_ready; db=f[0][0]; ws,user=db[2],db[1]
    r=service.read_relationships(ws,user); assert r['status']=='NOT_GENERATED'
    first=service.discover(ws,user)
    assert first['status']=='READY' and len(first['candidates'])==1
    candidate=first['candidates'][0];cid=candidate['candidate_id']
    assert candidate['evidence']['can_confirm'] and candidate['review_status']=='REVIEW_REQUIRED'
    assert service.discover(ws,user)==first
    accepted=service.review(ws,cid,user,'CONFIRMED',0)
    assert accepted['candidates'][0]['review_status']=='CONFIRMED'
    assert service.review(ws,cid,user,'CONFIRMED',0)==accepted
    with pytest.raises(RuntimeError):service.review(ws,cid,user,'REJECTED',0)
    rejected=service.review(ws,cid,user,'REJECTED',1)
    assert rejected['reviewed_relationships'][0]['relationship_version']==2
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.identity import get_current_user
    app.dependency_overrides[get_current_user]=lambda:user
    try:
        client=TestClient(app); path=f'/workspaces/{ws}/relationships'
        assert client.get(path).status_code==200
        assert client.get(path+'/readiness').json()['candidates']==[]
        assert client.post(path+'/discover',json={'state_ids':[1]}).status_code==422
        assert client.post(path+f'/candidates/{cid}/confirm',json={'expected_review_version':2,'score':100}).status_code==422
        assert all(secret not in client.get(path).text for secret in ('s3://','owner_key','manifest','source_signature','storage_path'))
    finally:app.dependency_overrides.pop(get_current_user,None)
    with db[0]() as conn:
        other=conn.execute("INSERT INTO workspaces(workspace_name,owner) VALUES ('Other','dev:test') RETURNING workspace_id").fetchone()[0]
    with pytest.raises(HTTPException):service.review(other,cid,user,'CONFIRMED',2)
    with pytest.raises(HTTPException):service.read_relationships(ws,{**user,'owner_key':'other'})
    with db[0]() as conn,pytest.raises(psycopg.Error):conn.execute("UPDATE relationship_candidates SET evidence='{}'")
    with db[0]() as conn,pytest.raises(psycopg.Error):conn.execute("DELETE FROM relationship_reviews")
    apply(f,second_delivery(f[0]))
    stale=service.read_relationships(ws,user)
    assert stale['status']=='STALE' and not stale['candidates'] and stale['reviewed_relationships'][0]['verification_status']=='STALE'
    with pytest.raises(ValueError,match='stale'):service.review(ws,cid,user,'CONFIRMED',2)
    refresh(f)
    newer=service.discover(ws,user)
    assert newer['status']=='READY' and newer['run_version']==2
    assert newer['reviewed_relationships'][0]['structural_status']=='CURRENT'
    assert newer['reviewed_relationships'][0]['verification_status']=='STALE'
    assert newer['candidates'][0]['review_status']=='REVIEW_REQUIRED'
    with db[0]() as conn:conn.execute("UPDATE datasets SET status='ARCHIVED' WHERE dataset_id=%s",(second[0][0][3],))
    assert service.read_relationships(ws,user)['reviewed_relationships'][0]['structural_status']=='UNAVAILABLE'

@pytest.mark.parametrize('change',['membership','state'])
def test_failure_retry_interruption_and_source_race(relationship_ready,change):
    f,_=relationship_ready; db=f[0][0];ws,user=db[2],db[1]; monkeypatch=f[2]
    original=service.load_frames
    monkeypatch.setattr(service,'load_frames',lambda *_:(_ for _ in ()).throw(ValueError('Artifact byte limit exceeded')))
    failed=service.discover(ws,user);assert failed['failure_code']=='LIMIT_EXCEEDED'
    with db[0]() as conn:conn.execute("UPDATE relationship_discovery_runs SET status='DISCOVERING',completed_at=NULL,failure_code=NULL")
    assert service.read_relationships(ws,user)['failure_code']=='INTERRUPTED'
    monkeypatch.setattr(service,'load_frames',original)
    recovered=service.discover(ws,user);assert recovered['status']=='READY' and recovered['run_version']==1
    with db[0]() as conn:conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES (%s,'Missing','dev:test')",(ws,))
    entered,release=Event(),Event()
    def held(*args):
        entered.set();assert release.wait(90)
        return original(*args)
    monkeypatch.setattr(service,'load_frames',held)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(service.discover,ws,user)
        try:
            assert entered.wait(90)
            with pytest.raises(RuntimeError,match='active'):service.discover(ws,user)
            if change=='state':apply(f,second_delivery(f[0]))
            else:
                with db[0]() as conn:conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES (%s,'Changed during verification','dev:test')",(ws,))
        finally:release.set()
        result=future.result(120)
    assert result['status']=='STALE' and not result['candidates']
    with db[0]() as conn:assert conn.execute('SELECT status,failure_code FROM relationship_discovery_runs ORDER BY run_id DESC LIMIT 1').fetchone()==('FAILED','SOURCE_CHANGED')

def test_concurrent_opposing_reviews_only_one_expected_version_wins(relationship_ready):
    f,_=relationship_ready;db=f[0][0];ws,user=db[2],db[1]
    r=service.discover(ws,user);cid=r['candidates'][0]['candidate_id']
    def act(status):
        try:service.review(ws,cid,user,status,0);return 'saved'
        except RuntimeError:return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(act,['CONFIRMED','REJECTED']))
    assert sorted(results)==['saved','stale']
    with db[0]() as conn:assert conn.execute('SELECT count(*) FROM relationship_reviews').fetchone()[0]==1
