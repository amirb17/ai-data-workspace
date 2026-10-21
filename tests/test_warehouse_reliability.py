"""11E: real disposable PostgreSQL metadata, bounded Parquet, deterministic crashes."""
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest
from fastapi import HTTPException

from tests.test_warehouse_foundation import pg, resource
from tests.test_warehouse_modeling import modeling_pg, configured, fact
from app.services import warehouse_model_service as models
from app.services.warehouse_reliability_service import ReliabilityService
from app.storage.incremental_artifacts import IncrementalArtifacts
from app.processing.append_engine import append_rows, LINEAGE, effective_schema
from app.processing.upsert_engine import upsert_rows, UPSERT_LINEAGE
from app.processing.snapshot_engine import snapshot_rows, SNAPSHOT_LINEAGE
from app.warehouse.accepted_changes import SourcePin, event, manifest, payload
from app.warehouse.reliability import (ProtocolError, Decision, Receipt, Result, ReferenceTarget, recover)
from app.warehouse.source_evidence import classify_bootstrap
from app.warehouse.scd_hashing import digest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'migrations/0031_warehouse_reliability.sql'
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Objects:
    def __init__(self): self.data = {}
    def get_object(self, Bucket, Key): return dict(Body=io.BytesIO(self.data[Key]))
    def put_object(self, Bucket, Key, Body, **kwargs):
        assert Key not in self.data
        self.data[Key] = Body


def pin(**updates):
    return SourcePin(**(dict(workspace_id=1, dataset_id=2, dataset_version_id=3, policy_id=4,
        rule_version=1, rule_sha256='a'*64, application_id=5, upload_id=6, source_dq_run_id=8, state_id=7,
        artifact_sha256='b'*64, strategy='UPSERT', accepted_at=NOW) | updates))


def sample(outcome='INSERTED', **row):
    return event(pin(), 'ledger-key:immutable-key', outcome, dict(id='A', value=Decimal('1.20')) | row, ('id','value'))


def test_event_identity_retry_payload_conflict_and_projection():
    a = sample(); b = sample(value=Decimal('2.0'))
    assert a.event_id == b.event_id and a.checksum != b.checksum
    assert a == sample(value=Decimal('1.200'))
    assert payload(dict(id='A', value=1, secret='excluded'), ('value','id')) == payload(dict(value=1,id='A'), ('id','value'))
    assert a.event_id == event(pin(accepted_at=NOW+timedelta(days=1)), a.evidence_key, a.outcome,
                               dict(id='A',value=Decimal('1.20')), ('id','value')).event_id


@pytest.mark.parametrize('value', [1.5, float('nan'), datetime(2026,1,1), {}, [], 'x'*65537],
                         ids=['float','nan','naive','mapping','list','oversized'])
def test_payload_fails_closed_on_lossy_or_unbounded_scalar(value):
    with pytest.raises(ValueError): payload(dict(id=value), ('id',))


def test_typed_scalar_domains_and_order_are_exact():
    values = dict(a=None,b=False,c=1,d=Decimal('1.2'),e=date(2026,1,1),f=NOW,g='é')
    assert [r[1][0] for r in payload(values, tuple(values))] == ['null','boolean','integer','decimal','date','timestamp','string']
    assert len({digest(payload(dict(x=x), ('x',))) for x in (1,Decimal(1),'1',True)}) == 4


def test_manifest_order_zero_material_and_zero_events():
    a,b = sample(), event(pin(), 'other', 'UNCHANGED', dict(id='B'), ('id',))
    assert manifest(pin(),1,1,'c'*64,[a,b],1) == manifest(pin(),1,1,'c'*64,[b,a],1)
    assert manifest(pin(),1,1,'c'*64,[b],1)['material_count'] == 0
    assert manifest(pin(),1,1,'c'*64,[],1)['event_count'] == 0
    with pytest.raises(ValueError): manifest(pin(),1,1,'c'*64,[a,a],1)


def test_metadata_mapping_order_and_manifest_round_trip_are_canonical():
    from app.warehouse.accepted_changes import digest as canonical_digest, verify_manifest
    assert canonical_digest(dict(a=dict(z=1,b=2),c=3))==canonical_digest(dict(c=3,a=dict(b=2,z=1)))
    m=manifest(pin(),1,1,'c'*64,[sample()],1)
    assert verify_manifest(json.loads(json.dumps(m,sort_keys=True)))==m


def test_reference_target_rejects_same_event_with_changed_payload():
    m=manifest(pin(),1,1,'c'*64,[sample()],1); target=ReferenceTarget()
    target.apply('token',[m])
    changed=json.loads(json.dumps(m)); changed['events'][0]['payload'][0][1][1]='changed'
    with pytest.raises(ValueError,match='conflict'): target.apply('token',[changed])


@pytest.mark.parametrize('exact,artifacts,current,result', [
    (True,True,True,'EXACT_ACCEPTED_EVIDENCE'),(False,True,True,'CURRENT_STATE_SEED_REQUIRES_INTENT'),
    (True,False,True,'CURRENT_STATE_SEED_REQUIRES_INTENT'),(False,False,False,'EXPLICIT_REBUILD_REQUIRED')])
def test_bootstrap_classification(exact,artifacts,current,result):
    assert classify_bootstrap(exact_chain=exact,artifacts_verified=artifacts,current_state_verified=current) == result


@pytest.fixture
def reliability_pg(modeling_pg, request):
    p = dict(modeling_pg)
    mode = getattr(request, 'param', 'APPEND')
    if mode != 'APPEND':
        with p['connect']() as conn:
            p['policy'] = conn.execute('''INSERT INTO dataset_load_policies(dataset_id,dataset_version_id,policy_version,
                load_strategy,business_keys,schema_columns,schema_hash,schema_evolution_policy,created_by,snapshot_coverage)
                SELECT dataset_id,dataset_version_id,2,%s,'["id"]',schema_columns,schema_hash,schema_evolution_policy,created_by,%s
                FROM dataset_load_policies WHERE policy_id=%s RETURNING policy_id''',
                (mode, 'COMPLETE' if mode=='SNAPSHOT' else None, p['policy'])).fetchone()[0]
    m,v,_,_ = configured(p, fact())
    models.approve_version(p['workspace'], m, v, p['owner'])
    r = resource(p,m,v)
    # A populated 0030 model/resource/source configuration exists before 0031.
    with p['connect']() as conn:
        old_tables = [r[0] for r in conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename')]
        def rows():
            return {t: conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(t))).fetchall() for t in old_tables}
        before = rows()
        def objects():
            queries = [
                "SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position",
                "SELECT c.relname,x.conname,pg_get_constraintdef(x.oid) FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid WHERE c.relnamespace=current_schema()::regnamespace ORDER BY c.relname,x.conname",
                "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE NOT t.tgisinternal AND c.relnamespace=current_schema()::regnamespace ORDER BY c.relname,t.tgname",
                "SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p WHERE p.pronamespace=current_schema()::regnamespace ORDER BY p.proname"]
            return [conn.execute(q).fetchall() for q in queries]
        old_definitions = objects()
        old_objects = conn.execute("""SELECT c.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x
            JOIN pg_class c ON c.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid
            WHERE c.relnamespace=current_schema()::regnamespace ORDER BY c.relname,i.relname""").fetchall()
        conn.execute(MIGRATION.read_text())
        assert rows() == before
        assert all(set(old) <= set(new) for old,new in zip(old_definitions,objects()))
        assert all(x in conn.execute("""SELECT c.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x
            JOIN pg_class c ON c.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid
            WHERE c.relnamespace=current_schema()::regnamespace""").fetchall() for x in old_objects)
    f = SimpleNamespace(pg=p, service=ReliabilityService(p['workspace'],m,v,p['owner']), model=m, version=v,
                        resource=r['resource_id'], store=IncrementalArtifacts(Objects()), mode=mode, frame=None, state=None)
    return f


def publish_source(f, records=None, *, coverage='COMPLETE', effective=NOW, published=True):
    """Synthetic accepted publication using real engines, constraints and artifacts."""
    p=f.pg; records = records if records is not None else [dict(id='A',line=1,segment='One',label='L',effective=NOW,float_id=1.5)]
    incoming=pd.DataFrame(records, columns=['id','line','segment','label','effective','float_id'])
    with p['connect']() as conn:
        c=conn.cursor(row_factory=dict_row)
        policy=c.execute('SELECT * FROM dataset_load_policies WHERE policy_id=%s',(p['policy'],)).fetchone()
        file=c.execute("INSERT INTO physical_files(file_name,file_size,file_hash,status) VALUES ('synthetic.csv',1,%s,'UPLOADED') RETURNING file_id",(hashlib.sha256(str(records).encode()+str(f.state).encode()).hexdigest(),)).fetchone()['file_id']
        upload=c.execute("INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING upload_id",(p['owner']['user_id'],p['workspace'],p['dataset'],file)).fetchone()['upload_id']
        assoc=c.execute('INSERT INTO dataset_version_files(dataset_version_id,file_id) VALUES (%s,%s) RETURNING dataset_version_file_id',(p['source_version'],file)).fetchone()['dataset_version_file_id']
        attempt=c.execute("INSERT INTO processing_attempts(file_id,dataset_version_file_id,attempt_number,stage,status) VALUES (%s,%s,1,'SILVER','SUCCESS') RETURNING attempt_id",(file,assoc)).fetchone()['attempt_id']
        dq=c.execute('''INSERT INTO data_quality_runs(file_id,dataset_version_file_id,attempt_id,rule_version,total_rows,valid_rows,rejected_rows)
            VALUES (%s,%s,%s,1,%s,%s,0) RETURNING dq_run_id''',(file,assoc,attempt,len(incoming),len(incoming))).fetchone()['dq_run_id']
        app=c.execute('''INSERT INTO delivery_applications(upload_request_id,dataset_id,dataset_version_id,dataset_version_file_id,
            policy_id,applied_rule_version,source_dq_run_id,ingestion_time,input_rows,valid_rows,rejected_rows,snapshot_coverage,snapshot_effective_at,delivery_kind)
            VALUES (%s,%s,%s,%s,%s,1,%s,NOW(),%s,%s,0,%s,%s,%s) RETURNING *''',
            (upload,p['dataset'],p['source_version'],assoc,p['policy'],dq,len(incoming),len(incoming),
             coverage if f.mode=='SNAPSHOT' else None,effective if f.mode=='SNAPSHOT' else None,'NORMAL' if f.mode=='SNAPSHOT' else None)).fetchone()
        app=c.execute("UPDATE delivery_applications SET status='RUNNING',started_at=NOW() WHERE application_id=%s RETURNING *",(app['application_id'],)).fetchone()
        columns=[x['name'] for x in policy['schema_columns']]
        lineage={'APPEND':LINEAGE,'UPSERT':UPSERT_LINEAGE,'SNAPSHOT':SNAPSHOT_LINEAGE}[f.mode]
        current=f.frame if f.frame is not None else pd.DataFrame(columns=columns+lineage)
        if f.mode=='SNAPSHOT': app['previous_snapshot_boundary_at']=f.boundary if hasattr(f,'boundary') else None
        candidate,counts,ledger={'APPEND':append_rows,'UPSERT':upsert_rows,'SNAPSHOT':snapshot_rows}[f.mode](current,incoming,policy,app)
        prefix=f"synthetic/application-{app['application_id']}/"
        silver=prefix+'delivery.parquet'; silver_hash=f.store.put(silver,f.store.parquet(incoming))
        dm=dict(app, policy=policy,silver_key=silver,silver_sha256=silver_hash)
        dk=prefix+'delivery.json'; dh=f.store.put(dk,f.store.json(dm))
        c.execute('''INSERT INTO delivery_manifests(application_id,manifest_key,manifest_sha256,silver_key,silver_sha256,effective_schema)
            VALUES (%s,%s,%s,%s,%s,%s)''',(app['application_id'],dk,dh,silver,silver_hash,Jsonb(effective_schema(incoming,columns))))
        data=prefix+'state.parquet'; data_hash=f.store.put(data,f.store.parquet(candidate))
        outcomes={'rejected':ledger} if f.mode=='APPEND' else dict(ledger=ledger,rejected=[x for x in ledger if x['outcome']=='REJECTED'])
        ok=prefix+'outcomes.json'; oh=f.store.put(ok,f.store.json(outcomes))
        sm={**{k:app[k] for k in ('application_id','dataset_id','dataset_version_id','policy_id','upload_request_id')},
            'previous_state_id':f.state,'data_key':data,'data_sha256':data_hash,'counts':counts,'outcomes_key':ok,'outcomes_sha256':oh}
        sk=prefix+'state.json'; sh=f.store.put(sk,f.store.json(sm))
        state=c.execute('''INSERT INTO dataset_state_versions(dataset_id,dataset_version_id,policy_id,source_application_id,previous_state_id,
            state_version,manifest_key,manifest_sha256,row_count,status,validated_at)
            VALUES (%s,%s,%s,%s,%s,(SELECT COALESCE(MAX(state_version),0)+2 FROM dataset_state_versions WHERE dataset_id=%s),%s,%s,%s,'VALIDATED',NOW()) RETURNING state_id''',
            (p['dataset'],p['source_version'],p['policy'],app['application_id'],f.state,p['dataset'],sk,sh,len(candidate))).fetchone()['state_id']
        if published:
            c.execute("UPDATE dataset_state_versions SET status='PUBLISHED',published_at=NOW() WHERE state_id=%s",(state,))
            c.execute('UPDATE datasets SET current_state_id=%s WHERE dataset_id=%s',(state,p['dataset']))
            c.execute("UPDATE delivery_applications SET status='SUCCESS',result_state_id=%s,completed_at=NOW(),current_state_rows=%s,snapshot_boundary_at=%s,snapshot_outcome=%s WHERE application_id=%s",(state,len(candidate),app.get('snapshot_boundary_at'),app.get('snapshot_outcome'),app['application_id']))
            f.frame=candidate; f.state=state; f.boundary=app.get('snapshot_boundary_at')
        return state


def registered(f, n=1):
    return [f.service.register(publish_source(f,[dict(id=str(i),line=1,segment='One',label='L',effective=NOW,float_id=1.5)]), f.store) for i in range(n)]


def intent(f, ms, kind='INITIAL_LOAD', key='request', expected=None):
    expected = expected or ReferenceTarget().apply('expected', ms)
    return f.service.create_intent(f.resource,kind,[m['manifest_id'] for m in ms],key,expected=expected,
                                   historical_range=[ms[0]['position'],ms[-1]['position']] if kind=='BACKFILL' else None)


def receipt(i, claim, result, outcome='SUCCEEDED'):
    return Receipt(operation_id=i['operation_id'],run_id=claim['run_id'],application_token=i['application_token'],
        intent_sha256=i['intent_sha256'],resource_generation=i['generation'],outcome=outcome,result=result)


def execute(f, i, ms, *, target=None):
    claim=f.service.claim(i['operation_id'],'worker')
    f.service.plan(i['operation_id'],claim['run_id'],claim['fence'])
    result=(target or ReferenceTarget()).apply(i['application_token'],ms)
    f.service.record_receipt(i['operation_id'],claim['run_id'],claim['fence'],receipt(i,claim,result))
    r=f.service.reconcile(i['operation_id'],claim['run_id'],claim['fence'])
    return f.service.publish(i['operation_id'],claim['run_id'],claim['fence'],r['reconciliation_id'])


def expire(f):
    with f.pg['connect']() as conn:
        conn.execute("UPDATE warehouse_writer_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE resource_id=%s",(f.resource,))


def test_discovery_registration_idempotency_and_zero_change(reliability_pg):
    f=reliability_pg; state=publish_source(f)
    assert [x['state_id'] for x in f.service.discover()]==[state]
    m=f.service.register(state,f.store)
    assert f.service.register(state,f.store)==m and not f.service.discover()
    assert f.service.pending(f.resource)==[m]
    assert m['position']==1 and m['event_count']==1
    zero=publish_source(f,[]); z=f.service.register(zero,f.store)
    assert z['event_count']==0 and z['position']==2
    p=execute(f,intent(f,[m,z]),[m,z])
    assert p['revision']==1 and f.service.read_progress(f.resource)['checkpoint']['position']==2
    assert f.service.pending(f.resource)==[]


def test_gap_blocks_and_failed_candidate_not_discovered(reliability_pg):
    f=reliability_pg; first=publish_source(f); second=publish_source(f)
    with pytest.raises(ProtocolError,match='SOURCE_GAP'): f.service.register(second,f.store)
    f.service.register(first,f.store); f.service.register(second,f.store)
    publish_source(f,published=False)
    assert not f.service.discover()
    rows=registered(f,1)
    with pytest.raises(ProtocolError,match='SOURCE_GAP'): intent(f,rows)


def test_publication_reconciliation_and_fencing(reliability_pg):
    f=reliability_pg; ms=registered(f); i=intent(f,ms); op=i['operation_id']
    a=f.service.claim(op,'a')
    with pytest.raises(ProtocolError,match='FENCED'): f.service.claim(op,'b')
    f.service.plan(op,a['run_id'],a['fence']); expire(f)
    b=f.service.claim(op,'b'); assert b['fence']==a['fence']+1 and b['decision']==Decision.RECONCILE_FIRST
    with pytest.raises(ProtocolError,match='FENCED'): f.service.record_receipt(op,a['run_id'],a['fence'],receipt(i,a,Result.model_validate(i['body']['expected'])))
    with pytest.raises(ProtocolError,match='RECONCILE_FIRST'): f.service.plan(op,b['run_id'],b['fence'])
    result=Result.model_validate(i['body']['expected'])
    f.service.record_receipt(op,b['run_id'],b['fence'],receipt(i,b,result))
    r=f.service.reconcile(op,b['run_id'],b['fence']); assert r['passed']
    p=f.service.publish(op,b['run_id'],b['fence'],r['reconciliation_id'])
    assert p['source_vector'][0]['position']==1
    with pytest.raises(ValueError,match='Completed'): f.service.claim(op,'c')


@pytest.mark.parametrize('outcome', ['FAILED','UNKNOWN','NOT_APPLIED','SUCCEEDED'])
def test_target_outcome_and_mismatch_block_publication(reliability_pg,outcome):
    f=reliability_pg; ms=registered(f); i=intent(f,ms); op=i['operation_id']; c=f.service.claim(op,'a')
    f.service.plan(op,c['run_id'],c['fence'])
    wrong=Result.model_validate(i['body']['expected']).model_copy(update=dict(checksum='0'*64))
    f.service.record_receipt(op,c['run_id'],c['fence'],receipt(i,c,wrong,outcome))
    if outcome=='FAILED':
        retry=f.service.claim(op,'retry')
        assert retry['run_id']!=c['run_id'] and retry['decision']==Decision.RETRY_SAFE
        assert f.service.read_progress(f.resource)==dict(head=None,checkpoint=None)
        return
    r=f.service.reconcile(op,c['run_id'],c['fence']); assert not r['passed']
    with pytest.raises(ValueError): f.service.publish(op,c['run_id'],c['fence'],r['reconciliation_id'])
    assert f.service.read_progress(f.resource)==dict(head=None,checkpoint=None)


@pytest.mark.parametrize('stage', ['registered','claimed','planned','applied','receipt','reconciled'])
def test_crash_restart_at_each_durable_stage(reliability_pg,stage):
    f=reliability_pg; ms=registered(f); i=intent(f,ms); op=i['operation_id']; target=ReferenceTarget()
    if stage=='registered':
        execute(f,i,ms,target=target); return
    a=f.service.claim(op,'a')
    if stage!='claimed': f.service.plan(op,a['run_id'],a['fence'])
    result=None
    if stage in ('applied','receipt','reconciled'): result=target.apply(i['application_token'],ms)
    if stage in ('receipt','reconciled'): f.service.record_receipt(op,a['run_id'],a['fence'],receipt(i,a,result))
    if stage=='reconciled': assert f.service.reconcile(op,a['run_id'],a['fence'])['passed']
    expire(f); b=f.service.claim(op,'b')
    if stage=='claimed': f.service.plan(op,b['run_id'],b['fence'])
    elif result is None:
        # Explicit token lookup proves NOT_APPLIED, then reapplication is safe.
        assert target.lookup(i['application_token']) is None
        f.service.record_receipt(op,b['run_id'],b['fence'],receipt(i,b,None,'NOT_APPLIED'))
        f.service.plan(op,b['run_id'],b['fence'])
    result=target.lookup(i['application_token']) or target.apply(i['application_token'],ms)
    f.service.record_receipt(op,b['run_id'],b['fence'],receipt(i,b,result))
    r=f.service.reconcile(op,b['run_id'],b['fence'])
    f.service.publish(op,b['run_id'],b['fence'],r['reconciliation_id'])
    assert len(target.applications)==1 and f.service.read_progress(f.resource)['checkpoint']['position']==1


def test_replay_backfill_and_intent_idempotency(reliability_pg):
    f=reliability_pg; ms=registered(f,3); initial=intent(f,ms)
    assert intent(f,ms)['operation_id']==initial['operation_id']
    assert intent(f,ms,key='rediscovery')['operation_id']==initial['operation_id']
    execute(f,initial,ms)
    assert intent(f,ms)['operation_id']==initial['operation_id']
    for kind in ('REPLAY','BACKFILL'):
        i=intent(f,ms[:1],kind,key=kind); execute(f,i,ms[:1])
        progress=f.service.read_progress(f.resource)
        assert progress['checkpoint']['position']==3
        assert progress['head']['revision']==(2 if kind=='REPLAY' else 3)


def test_publication_cas_and_atomic_rollback(reliability_pg,monkeypatch):
    f=reliability_pg; ms=registered(f); a=intent(f,ms,key='a'); b=intent(f,ms,'REBUILD',key='b')
    from app.db import warehouse_repository
    transition=warehouse_repository.transition
    def fail(conn,run,req):
        if req.status=='SUCCEEDED': raise RuntimeError('crash after head and checkpoint writes')
        return transition(conn,run,req)
    monkeypatch.setattr(warehouse_repository,'transition',fail)
    with pytest.raises(RuntimeError): execute(f,a,ms)
    assert f.service.read_progress(f.resource)==dict(head=None,checkpoint=None)
    with f.pg['connect']() as conn: assert conn.execute('SELECT count(*) FROM warehouse_publications').fetchone()[0]==0
    monkeypatch.setattr(warehouse_repository,'transition',transition)
    expire(f)
    with pytest.raises(ProtocolError,match='RECONCILE_FIRST'): f.service.claim(b['operation_id'],'different-intent')
    c=f.service.claim(a['operation_id'],'recover')
    result=Result.model_validate(a['body']['expected'])
    f.service.record_receipt(a['operation_id'],c['run_id'],c['fence'],receipt(a,c,result))
    r=f.service.reconcile(a['operation_id'],c['run_id'],c['fence'])
    f.service.publish(a['operation_id'],c['run_id'],c['fence'],r['reconciliation_id'])
    d=f.service.claim(b['operation_id'],'stale-candidate')
    with pytest.raises(ProtocolError,match='SOURCE_GAP'): f.service.plan(b['operation_id'],d['run_id'],d['fence'])
    # Explicit durable fixture for a stale externally prepared candidate: even
    # if such evidence arrives, final publication must independently CAS again.
    from app.db.warehouse_reliability_repository import journal
    with f.pg['connect']() as conn: journal(conn,d['run_id'],d['fence'],'PLANNED',dict(application_token=b['application_token']))
    f.service.record_receipt(b['operation_id'],d['run_id'],d['fence'],receipt(b,d,result))
    rb=f.service.reconcile(b['operation_id'],d['run_id'],d['fence'])
    with pytest.raises(ProtocolError,match='SOURCE_GAP'): f.service.publish(b['operation_id'],d['run_id'],d['fence'],rb['reconciliation_id'])


def test_db_enforces_checkpoint_head_atomicity_and_append_only(reliability_pg):
    f=reliability_pg; ms=registered(f); execute(f,intent(f,ms),ms)
    for stmt in ('UPDATE warehouse_checkpoints SET position=99,version=version+1', 'DELETE FROM warehouse_checkpoints',
                 'UPDATE warehouse_publication_heads SET revision=99', 'DELETE FROM warehouse_publications',
                 'UPDATE warehouse_accepted_manifests SET checksum=checksum','TRUNCATE warehouse_outbox'):
        with pytest.raises(psycopg.Error), f.pg['connect']() as conn: conn.execute(stmt)


def test_cross_workspace_source_model_and_resource_isolation(reliability_pg):
    f=reliability_pg; ms=registered(f)
    other=ReliabilityService(f.pg['workspace'],f.model,f.version,f.pg['other'])
    with pytest.raises(HTTPException): other.discover()
    with pytest.raises(ProtocolError): f.service.create_intent(9999,'INITIAL_LOAD',[ms[0]['manifest_id']],'x',expected=ReferenceTarget().apply('x',ms))
    with pytest.raises(ProtocolError): f.service.create_intent(f.resource,'INITIAL_LOAD',['0'*64],'x',expected=ReferenceTarget().apply('x',ms))


@pytest.mark.parametrize('reliability_pg',['UPSERT','SNAPSHOT'],indirect=True)
def test_actual_upsert_snapshot_unchanged_observation_and_deactivation(reliability_pg):
    f=reliability_pg; first=f.service.register(publish_source(f),f.store)
    unchanged=f.service.register(publish_source(f,effective=NOW+timedelta(days=1)),f.store)
    assert unchanged['material_count']==0 and unchanged['event_count']==1
    assert unchanged['events'][0]['outcome']=='UNCHANGED'
    if f.mode=='SNAPSHOT':
        deactivated=f.service.register(publish_source(f,[],effective=NOW+timedelta(days=2)),f.store)
        assert [x['outcome'] for x in deactivated['events']]==['DEACTIVATED']
    execute(f,intent(f,[first,unchanged]),[first,unchanged])


def test_tampered_published_artifact_fails_registration(reliability_pg):
    f=reliability_pg; state=publish_source(f)
    key=next(k for k in f.store.client.data if k.endswith('state.parquet'))
    f.store.client.data[key]=b'broken'
    with pytest.raises(ProtocolError,match='SOURCE_DRIFT'): f.service.register(state,f.store)
    assert f.service.discover()
    with f.pg['connect']() as conn: assert conn.execute('SELECT count(*) FROM warehouse_outbox').fetchone()[0]==0


def test_reference_replay_uses_actual_scd_keys_and_history():
    from tests.test_warehouse_scd import context,event as scd_event,JAN,JUN
    from app.warehouse.scd2 import plan_scd2
    from app.warehouse.scd_reference import apply_plan
    from app.warehouse.scd_hashing import state_digest
    c=context(); events=[scd_event(c,JAN),scd_event(c,JUN,'Mumbai')]
    states=[]
    def transition(_):
        state=None
        for e in events: state=apply_plan(state,plan_scd2(c,state,e)).state
        states.append(state)
        return state_digest(state)
    m=manifest(pin(),1,1,'c'*64,[sample()],1); target=ReferenceTarget()
    a=target.apply('normal',[m],transition); b=target.apply('replay',[m],transition)
    assert a==b and states[0]==states[1] and len(states[0].versions)==2
    assert target.apply('normal',[m],transition)==a and len(states)==2


@pytest.mark.parametrize('reliability_pg',['UPSERT','SNAPSHOT'],indirect=True)
def test_source_rejects_conflicts_and_partial_absence_never_become_events(reliability_pg):
    f=reliability_pg
    f.service.register(publish_source(f),f.store)
    base=dict(id='B',line=1,segment='One',label='L',effective=NOW,float_id=1.5)
    rejected=f.service.register(publish_source(f,[base,dict(base,segment='Conflict')],coverage='PARTIAL',effective=NOW+timedelta(days=1)),f.store)
    assert rejected['event_count']==0
    if f.mode=='SNAPSHOT':
        assert rejected['pin']['coverage']=='PARTIAL'
        assert f.frame['_datarise_active'].tolist()==[True]


def test_lost_response_unknown_requires_lookup_and_can_block_manual_review(reliability_pg):
    f=reliability_pg; ms=registered(f); i=intent(f,ms); op=i['operation_id']; c=f.service.claim(op,'a')
    f.service.plan(op,c['run_id'],c['fence'])
    f.service.record_receipt(op,c['run_id'],c['fence'],receipt(i,c,None,'UNKNOWN'))
    with pytest.raises(ProtocolError,match='RECONCILE_FIRST'): f.service.plan(op,c['run_id'],c['fence'])
    f.service.manual_review(op,c['run_id'],c['fence']); expire(f)
    with pytest.raises(ProtocolError,match='MANUAL_REVIEW'): f.service.claim(op,'b')
    other=intent(f,ms,'REBUILD',key='cannot-bypass-review')
    with pytest.raises(ProtocolError,match='MANUAL_REVIEW'): f.service.claim(other['operation_id'],'different-intent')
    assert f.service.read_progress(f.resource)['checkpoint'] is None


def test_result_pins_rebuild_bounds_and_broken_manifest_chain(reliability_pg):
    f=reliability_pg; ms=registered(f,4)
    with pytest.raises(ProtocolError,match='SOURCE_GAP'): intent(f,[ms[0],ms[1],ms[3]])
    with pytest.raises(ValueError): f.service.create_intent(f.resource,'BACKFILL',[ms[0]['manifest_id']],'bad',expected=ReferenceTarget().apply('x',ms[:1]),historical_range=[1,2])
    i=intent(f,ms); c=f.service.claim(i['operation_id'],'a'); f.service.plan(i['operation_id'],c['run_id'],c['fence'])
    bad=receipt(i,c,Result.model_validate(i['body']['expected'])).model_copy(update=dict(resource_generation=i['generation']+1))
    with pytest.raises(ProtocolError,match='RESOURCE_DRIFT'): f.service.record_receipt(i['operation_id'],c['run_id'],c['fence'],bad)
    good=receipt(i,c,Result.model_validate(i['body']['expected']))
    f.service.record_receipt(i['operation_id'],c['run_id'],c['fence'],good)
    r=f.service.reconcile(i['operation_id'],c['run_id'],c['fence'])
    f.service.publish(i['operation_id'],c['run_id'],c['fence'],r['reconciliation_id'])
    with pytest.raises(ValueError,match='unconsumed'): intent(f,ms,'REBUILD',key='rebuild')
    rebuild_resource=resource(f.pg,f.model,f.version,table='synthetic_generation_2',generation=2)
    rebuilt=f.service.create_intent(rebuild_resource['resource_id'],'REBUILD',[m['manifest_id'] for m in ms],'rebuild',expected=ReferenceTarget().apply('x',ms))
    assert rebuilt['generation']==2 and rebuilt['body']['prior_position']==0


def test_stale_reconciliation_cannot_publish_after_new_receipt(reliability_pg):
    f=reliability_pg; ms=registered(f); i=intent(f,ms); c=f.service.claim(i['operation_id'],'a')
    op=i['operation_id']; f.service.plan(op,c['run_id'],c['fence'])
    f.service.record_receipt(op,c['run_id'],c['fence'],receipt(i,c,Result.model_validate(i['body']['expected'])))
    r=f.service.reconcile(op,c['run_id'],c['fence'])
    f.service.record_receipt(op,c['run_id'],c['fence'],receipt(i,c,None,'UNKNOWN'))
    with pytest.raises(ValueError,match='superseded'): f.service.publish(op,c['run_id'],c['fence'],r['reconciliation_id'])


def test_source_latest_policy_does_not_reinterpret_old_evidence_and_archive_fails_closed(reliability_pg):
    f=reliability_pg; state=publish_source(f)
    with f.pg['connect']() as conn:
        conn.execute('''INSERT INTO dataset_load_policies(dataset_id,dataset_version_id,policy_version,load_strategy,business_keys,
            schema_columns,schema_hash,schema_evolution_policy,created_by)
            SELECT dataset_id,dataset_version_id,99,load_strategy,business_keys,schema_columns,schema_hash,schema_evolution_policy,created_by
            FROM dataset_load_policies WHERE policy_id=%s''',(f.pg['policy'],))
    assert f.service.register(state,f.store)['pin']['policy_id']==f.pg['policy']
    models.archive_model(f.pg['workspace'],f.model,f.pg['owner'])
    with pytest.raises((HTTPException,ProtocolError)): f.service.discover()


def test_disabled_service_stops_before_database(monkeypatch):
    from app.services import warehouse_reliability_service as service
    monkeypatch.setenv('WAREHOUSE_ENABLED','false')
    monkeypatch.setattr(service,'repository_transaction',lambda:pytest.fail('Disabled access to DB'))
    with pytest.raises(RuntimeError): ReliabilityService(1,1,1,{}).discover()


def test_registration_outbox_failure_rolls_back_then_discovery_recovers(reliability_pg,monkeypatch):
    from app.db import warehouse_reliability_repository as repository
    f=reliability_pg; state=publish_source(f); register=repository.register
    def interrupted(*args):
        register(*args)
        raise RuntimeError('crash before registration commit')
    monkeypatch.setattr(repository,'register',interrupted)
    with pytest.raises(RuntimeError): f.service.register(state,f.store)
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_accepted_manifests').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM warehouse_outbox').fetchone()[0]==0
    assert [r['state_id'] for r in f.service.discover()]==[state]
    monkeypatch.setattr(repository,'register',register)
    assert f.service.register(state,f.store)['position']==1


def test_migration_preserves_populated_0030_source_and_gold(modeling_pg):
    p=dict(modeling_pg); m,v,_,_=configured(p,fact())
    models.approve_version(p['workspace'],m,v,p['owner']); resource(p,m,v)
    f=SimpleNamespace(pg=p,mode='APPEND',store=IncrementalArtifacts(Objects()),frame=None,state=None)
    state=publish_source(f)
    with p['connect']() as conn:
        gold=conn.execute('''INSERT INTO dataset_gold_runs(dataset_id,source_state_id,dataset_version_id,policy_id,
            source_sha256,status,completed_at,manifest_key,manifest_sha256,catalog,row_count)
            SELECT dataset_id,state_id,dataset_version_id,policy_id,manifest_sha256,'SUCCESS',NOW(),
            'synthetic-gold-manifest',%s,'[]',row_count FROM dataset_state_versions WHERE state_id=%s RETURNING gold_run_id''',
            ('c'*64,state)).fetchone()[0]
        conn.execute("UPDATE datasets SET current_gold_run_id=%s,state_analytics_status='FRESH' WHERE dataset_id=%s",(gold,p['dataset']))
        tables=[x[0] for x in conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename')]
        def snapshot():
            return {t:conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(t))).fetchall() for t in tables}
        before=snapshot(); conn.execute(MIGRATION.read_text())
        assert snapshot()==before
        assert conn.execute('SELECT current_state_id,current_gold_run_id,state_analytics_status FROM datasets WHERE dataset_id=%s',(p['dataset'],)).fetchone()==(state,gold,'FRESH')
        assert conn.execute('SELECT count(*) FROM warehouse_outbox').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM warehouse_accepted_manifests').fetchone()[0]==0
        assert conn.execute('''SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
            WHERE c.relnamespace=current_schema()::regnamespace AND (NOT i.indisvalid OR NOT i.indisready)''').fetchone()[0]==0


@pytest.mark.parametrize('outcome',[None,'UNKNOWN','SUCCEEDED'])
def test_different_intent_cannot_bypass_prior_uncertain_or_unpublished_work(reliability_pg,outcome):
    f=reliability_pg; ms=registered(f); a=intent(f,ms); b=intent(f,ms,'REBUILD',key='other')
    c=f.service.claim(a['operation_id'],'a'); f.service.plan(a['operation_id'],c['run_id'],c['fence'])
    if outcome:
        result=Result.model_validate(a['body']['expected']) if outcome=='SUCCEEDED' else None
        f.service.record_receipt(a['operation_id'],c['run_id'],c['fence'],receipt(a,c,result,outcome))
    expire(f)
    with pytest.raises(ProtocolError,match='RECONCILE_FIRST'): f.service.claim(b['operation_id'],'b')
    recovery=f.service.claim(a['operation_id'],'recover-original')
    assert recovery['decision']==(Decision.ALREADY_APPLIED if outcome=='SUCCEEDED' else Decision.RECONCILE_FIRST)
