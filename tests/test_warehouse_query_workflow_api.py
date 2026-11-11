"""Actual authenticated HTTP lifecycle + isolated PostgreSQL; offline provider only."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType, SimpleNamespace

from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound
from google.cloud.bigquery.job import QueryJob
from psycopg import DatabaseError
from psycopg.rows import dict_row
import pytest

from app.auth import service as auth
from app.auth.security import new_csrf
from app.query_governance.types import QueryEstimate
from app.services import warehouse_query_workflow_service as workflow
from app.services import warehouse_sql_provider_policy_service as policy
from app.services import warehouse_sql_recovery_attestation_service as attest
from app.warehouse.query_provider_policy import ProviderPolicy
from app.warehouse.query_recovery_attestation import LookupPolicy,LookupRuntime,LookupOnlyClient
from tests.test_warehouse_sql_trusted_recompilation_414d4e import (
    pg,modeling_pg,fact_provider_pg,genuinely_served_query,
)
from tests.test_warehouse_sql_dispatch_checkpoint_414d5 import change

ROOT=Path(__file__).resolve().parents[1]
CREATOR='synthetic-query@synthetic-project.iam.gserviceaccount.com'


class FakeBigQuery:
    project='synthetic-project'
    location='asia-south1'
    def __init__(self):
        self.jobs={};self.inserts=[];self.lookups=[];self.cancels=[];self.result_reads=[]
        self.estimate_bytes=10*1024*1024;self.next_state='DONE';self.error=None
        self.lose_insert=False;self.absent_insert=False;self.lookup_error=False
        self.before_insert=None;self.before_lookup=None;self.before_results=None
        self.bad_page=False;self.bad_job=False;self.missing_billing=False
    def estimate(self,query):
        assert query.compiler_version==2 and query.sql.startswith('SELECT ')
        return QueryEstimate(self.estimate_bytes)
    def insert_job_once(self,*,project,location,body):
        assert (project,location)==(self.project,self.location)
        self.inserts.append(deepcopy(body))
        if self.before_insert:self.before_insert()
        if self.absent_insert:raise TimeoutError('Synthetic failure before provider receives request')
        job=body['jobReference']['jobId'];assert job not in self.jobs
        now=int(datetime.now(timezone.utc).timestamp()*1000)
        table=body['configuration']['query']['query'].split(' FROM `')[1].split('`')[0]
        p,d,t=table.split('.')
        raw=dict(kind='bigquery#job',jobReference=deepcopy(body['jobReference']),user_email=CREATOR,
            configuration=deepcopy(body['configuration']),status=dict(state=self.next_state),
            statistics=dict(creationTime=str(now),startTime=str(now),query=dict(statementType='SELECT',
                referencedTables=[dict(projectId=p,datasetId=d,tableId=t)],cacheHit=False,
                totalBytesProcessed='1024',totalBytesBilled='10485760')))
        if self.next_state=='DONE':raw['statistics']['endTime']=str(now)
        if self.error:raw['status']['errorResult']=dict(reason=self.error)
        if self.missing_billing:raw['statistics']['query'].pop('totalBytesBilled')
        self.jobs[job]=raw
        if self.lose_insert:raise TimeoutError('Synthetic response loss after provider accepted')
    def get_job(self,job_id,**kwargs):
        assert kwargs==dict(project=self.project,location=self.location,retry=None,timeout=10)
        self.lookups.append(job_id)
        if self.before_lookup:self.before_lookup()
        if self.lookup_error:raise TimeoutError('Synthetic lookup response loss')
        if job_id not in self.jobs:raise NotFound('Synthetic missing job')
        raw=deepcopy(self.jobs[job_id])
        if self.bad_job:raw['configuration']['labels']['workspace']='999999'
        return QueryJob.from_api_repr(raw,SimpleNamespace(project=self.project))
    def finish(self,job,state='DONE',error=None):
        raw=self.jobs[job];raw['status']=dict(state=state)
        if state=='DONE':raw['statistics']['endTime']=str(int(datetime.now(timezone.utc).timestamp()*1000))
        if error:raw['status']['errorResult']=dict(reason=error)
    def cancel_job_once(self,*,project,location,job_id):
        assert (project,location)==(self.project,self.location)
        self.cancels.append(job_id);self.finish(job_id,error='stopped')
    def get_query_results_once(self,*,project,location,job_id,max_results):
        assert (project,location)==(self.project,self.location)
        self.result_reads.append(job_id)
        if self.before_results:self.before_results()
        return dict(kind='bigquery#getQueryResultsResponse',jobReference=dict(projectId=project,location=location,jobId=job_id),
            jobComplete=True,schema=dict(fields=[dict(name='forged' if self.bad_page else 'id',type='STRING',mode='NULLABLE')]),
            rows=[dict(f=[dict(v='A')]),dict(f=[dict(v='B')])],totalRows='2')


@pytest.fixture
def api_case(genuinely_served_query,monkeypatch):
    f,pub,receipt,g,a,r,q=genuinely_served_query
    g.release(r)  # Unrelated fixture reservation must not consume this API's budget.
    monkeypatch.setenv('AUTH_MODE','session');monkeypatch.setenv('AUTH_COOKIE_SECURE','false')
    monkeypatch.setenv('AUTH_SECRET','synthetic-dispatch-session-test-secret-414d5')
    from app.main import app
    with f.pg['connect']() as conn:
        for migration in sorted((ROOT/'migrations').glob('*.sql')):
            if '0053'<=migration.name[:4]<='0061':conn.execute(migration.read_text(encoding='utf-8-sig'))
        token=auth.issue_session(conn.cursor(row_factory=dict_row),f.pg['owner']['user_id'])
    fake=FakeBigQuery()
    monkeypatch.setattr(policy,'_POLICIES',MappingProxyType({f.pg['workspace']:ProviderPolicy(fake.project,CREATOR,'test-workflow-1')}))
    monkeypatch.setattr(attest,'_RUNTIMES',MappingProxyType({(fake.project,fake.location):
        LookupRuntime(LookupPolicy(fake.project,fake.location,CREATOR,'test-lookup-1'),LookupOnlyClient(fake))}))
    runtime=workflow.InjectedQueryRuntime(f.cloud,fake,fake,fake,fake,lease_seconds=3)
    app.dependency_overrides[workflow.get_query_runtime]=lambda:runtime
    def client(cookie=token):
        c=TestClient(app);csrf=new_csrf();c.cookies.set('datarise_session',cookie);c.cookies.set('datarise_csrf',csrf)
        c.headers.update({'Origin':'http://localhost:5173','X-CSRF-Token':csrf})
        return c
    s=SimpleNamespace(f=f,pub=pub,token=token,fake=fake,runtime=runtime,app=app,client_factory=client,
        base=f'/workspaces/{f.pg["workspace"]}/warehouse/queries',sql=f'SELECT id FROM model_{f.model} LIMIT 5')
    s.client=client()
    try:yield s
    finally:app.dependency_overrides.pop(workflow.get_query_runtime,None);s.client.close()


def post(s,path='',**values):return s.client.post(s.base+path,json=values or None)
def admit(s,key='request_key_000001'):
    response=post(s,model_id=s.f.model,sql=s.sql,idempotency_key=key)
    assert response.status_code==201,response.text
    return response.json()['attempt_handle']
def count(s,table):
    with s.f.pg['connect']() as conn:return conn.execute('SELECT count(*) FROM '+table).fetchone()[0]
def wait_lease(s):
    with s.f.pg['connect']() as conn:
        conn.execute("SELECT pg_sleep(LEAST(4,GREATEST(0,EXTRACT(epoch FROM max(lease_until)-clock_timestamp()))+0.02)) FROM warehouse_query_send_journal")


def test_full_authenticated_model_to_exact_bounded_results(api_case):
    s=api_case
    catalog=s.client.get(f'/workspaces/{s.f.pg["workspace"]}/warehouse/catalog')
    assert catalog.status_code==200 and any(m['model_id']==s.f.model for m in catalog.json()['models'])
    check=post(s,'/validate',model_id=s.f.model,sql=s.sql)
    assert check.status_code==200 and check.json()['budget_decision']=='ALLOWED'
    h=admit(s)
    assert s.client.get(s.base+'/'+h).json()['status']=='ADMITTED'
    assert s.client.get(s.base+'/'+h+'/results').status_code==409
    done=post(s,'/'+h+'/execute');assert done.status_code==200,done.text
    assert done.json()['status']=='SUCCEEDED'
    page=s.client.get(s.base+'/'+h+'/results');assert page.status_code==200,page.text
    assert page.json()['rows']==[['A'],['B']] and page.json()['columns']==['id']
    assert 'synthetic-project' not in page.text and 'dr_query_' not in page.text
    assert not page.json()['query_execution_enabled']
    assert count(s,'query_governance_usage')==1 and len(s.fake.inserts)==1
    with s.f.pg['connect']() as conn:
        assert conn.execute('SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id'])).fetchone()[0]


def test_duplicate_admission_execution_and_settlement_reuse_original(api_case):
    s=api_case;h=admit(s)
    assert admit(s)==h
    for _ in range(2):
        result=post(s,'/'+h+'/execute');assert result.status_code==200,result.text
        assert result.json()['status']=='SUCCEEDED'
    assert count(s,'warehouse_query_api_requests')==1 and count(s,'query_governance_usage')==1
    assert len(s.fake.inserts)==1
    bad=post(s,model_id=s.f.model,sql=s.sql.replace('5','4'),idempotency_key='request_key_000001')
    assert bad.status_code==409


@pytest.mark.parametrize('error,expected',[('invalidQuery','FAILED'),('deadlineExceeded','FAILED'),('stopped','CANCELLED')])
def test_failed_timedout_and_cancelled_jobs_are_not_success(api_case,error,expected):
    s=api_case;s.fake.error=error;h=admit(s)
    reply=post(s,'/'+h+'/execute');assert reply.status_code==200,reply.text
    assert reply.json()['status']==expected and not reply.json()['results_available']
    assert s.client.get(s.base+'/'+h+'/results').status_code==409
    assert not s.fake.result_reads and count(s,'query_governance_usage')==1


@pytest.mark.parametrize('mode',['lost_insert_response','missing_job','lookup_timeout','bad_metadata','missing_billing'])
def test_uncertain_and_incomplete_jobs_never_resubmit_or_invent_success(api_case,mode):
    s=api_case
    if mode=='lost_insert_response':s.fake.lose_insert=True
    elif mode=='missing_job':s.fake.absent_insert=True
    elif mode=='lookup_timeout':s.fake.lookup_error=True
    elif mode=='bad_metadata':s.fake.bad_job=True
    else:s.fake.error='stopped';s.fake.missing_billing=True
    h=admit(s);reply=post(s,'/'+h+'/execute');assert reply.status_code==200,reply.text
    expected='SUCCEEDED' if mode=='lost_insert_response' else 'UNCERTAIN'
    assert reply.json()['status']==expected
    if expected=='UNCERTAIN':
        assert count(s,'query_governance_usage')==0
        wait_lease(s);again=post(s,'/'+h+'/execute');assert again.status_code==200,again.text
        assert again.json()['status']=='UNCERTAIN' and len(s.fake.inserts)==1


def test_pending_refresh_and_cancel_are_fenced_and_idempotent(api_case):
    s=api_case;s.fake.next_state='RUNNING';h=admit(s)
    first=post(s,'/'+h+'/execute');assert first.status_code==200,first.text
    assert first.json()['status']=='RUNNING' and not first.json()['results_available']
    assert post(s,'/'+h+'/refresh').status_code==409
    wait_lease(s)
    reply=post(s,'/'+h+'/cancel');assert reply.status_code==200,reply.text
    assert reply.json()['status']=='CANCELLED'
    assert post(s,'/'+h+'/cancel').json()['status']=='CANCELLED'
    assert len(s.fake.cancels)==1 and len(s.fake.inserts)==1


def test_completion_persistence_failure_recovers_without_second_provider_call(api_case,monkeypatch):
    s=api_case;h=admit(s);original=workflow.settle_verified_warehouse_terminal
    def fail(**kwargs):raise RuntimeError('Synthetic pre-settlement crash')
    with monkeypatch.context() as patch:
        patch.setattr(workflow,'settle_verified_warehouse_terminal',fail)
        assert post(s,'/'+h+'/execute').status_code==503
    assert s.client.get(s.base+'/'+h).json()['status']=='UNCERTAIN'
    reply=post(s,'/'+h+'/refresh');assert reply.status_code==200,reply.text
    assert reply.json()['status']=='SUCCEEDED' and len(s.fake.lookups)==1
    assert len(s.fake.inserts)==1 and count(s,'query_governance_usage')==1


@pytest.mark.parametrize('sql',['DELETE FROM model_1','SELECT * FROM model_1','SELECT id FROM model_1; DROP TABLE users'])
def test_unreviewed_sql_rejected_without_admission(api_case,sql):
    s=api_case;reply=post(s,model_id=s.f.model,sql=sql,idempotency_key='unsafe_query_00001')
    assert reply.status_code in (400,422) and count(s,'warehouse_query_api_requests')==0
    assert not s.fake.inserts


def test_budget_failure_and_provider_disabled_are_explicit(api_case):
    s=api_case;s.fake.estimate_bytes=2**30
    assert post(s,model_id=s.f.model,sql=s.sql,idempotency_key='over_budget_00001').status_code==409
    assert count(s,'warehouse_query_api_requests')==0
    s.app.dependency_overrides.pop(workflow.get_query_runtime)
    assert post(s,'/validate',model_id=s.f.model,sql=s.sql).status_code==503
    assert not s.fake.inserts


def test_auth_csrf_extra_fields_foreign_scope_and_original_session(api_case):
    s=api_case;h=admit(s)
    with s.client_factory('bad-cookie') as c:assert c.get(s.base+'/'+h).status_code==401
    with s.f.pg['connect']() as conn:
        newer=auth.issue_session(conn.cursor(row_factory=dict_row),s.f.pg['owner']['user_id'])
    with s.client_factory(newer) as c:assert c.get(s.base+'/'+h).status_code==403
    assert s.client.get(s.base.replace('/'+str(s.f.pg['workspace'])+'/', '/'+str(s.f.pg['foreign_workspace'])+'/')+'/'+h).status_code==403
    bad=post(s,model_id=s.f.model,sql=s.sql,idempotency_key='extra_fields_00001',owner_key='client-secret-value')
    assert bad.status_code==422 and 'client-secret-value' not in bad.text
    headers=dict(s.client.headers);s.client.headers.pop('X-CSRF-Token')
    assert post(s,'/'+h+'/execute').status_code==403
    s.client.headers.update(headers)
    assert not s.fake.inserts


def test_revocation_during_provider_lookup_never_releases_results(api_case):
    s=api_case;h=admit(s)
    def revoke():
        with s.f.pg['connect']() as conn:conn.execute("UPDATE users SET account_status='DISABLED' WHERE user_id=%s",(s.f.pg['owner']['user_id'],))
    s.fake.before_lookup=revoke
    assert post(s,'/'+h+'/execute').status_code in (401,403)
    assert count(s,'query_governance_usage')==0 and not s.fake.result_reads


def test_revocation_and_schema_corruption_during_result_read_fail_closed(api_case):
    s=api_case;h=admit(s);assert post(s,'/'+h+'/execute').json()['status']=='SUCCEEDED'
    s.fake.bad_page=True;assert s.client.get(s.base+'/'+h+'/results').status_code in (400,503)
    s.fake.bad_page=False
    def revoke():
        with s.f.pg['connect']() as conn:conn.execute("UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE user_id=%s",(s.f.pg['owner']['user_id'],))
    s.fake.before_results=revoke
    assert s.client.get(s.base+'/'+h+'/results').status_code in (401,403)


def test_concurrent_execute_calls_overlap_but_submit_once(api_case):
    s=api_case;h=admit(s);entered,release=Event(),Event()
    def pause():
        entered.set()
        if not release.wait(20):raise TimeoutError('test release missing')
    s.fake.before_insert=pause
    with ThreadPoolExecutor(max_workers=2) as pool:
        with s.client_factory() as first,s.client_factory() as second:
            future=pool.submit(first.post,s.base+'/'+h+'/execute')
            try:
                assert entered.wait(10)
                with s.f.pg['connect']() as conn:
                    phase,held=conn.execute('SELECT phase,lease_until>clock_timestamp() FROM warehouse_query_send_journal ORDER BY revision DESC LIMIT 1').fetchone()
                    assert phase=='SEND_UNKNOWN' and held
                    assert conn.execute("SELECT count(*) FROM query_governance_reservations WHERE status='EXECUTING'").fetchone()[0]==1
                rival=second.post(s.base+'/'+h+'/execute')
                assert rival.status_code==409,rival.text
            finally:release.set()
            reply=future.result(timeout=20);assert reply.status_code==200,reply.text
    assert len(s.fake.inserts)==1 and count(s,'query_governance_usage')==1


def test_missing_terminal_billing_can_be_recovered_without_resubmission(api_case):
    s=api_case;s.fake.missing_billing=True;h=admit(s)
    assert post(s,'/'+h+'/execute').json()['status']=='UNCERTAIN'
    assert count(s,'query_governance_usage')==0
    job=next(iter(s.fake.jobs));s.fake.jobs[job]['statistics']['query']['totalBytesBilled']='10485760'
    wait_lease(s)
    reply=post(s,'/'+h+'/refresh');assert reply.status_code==200,reply.text
    assert reply.json()['status']=='SUCCEEDED'
    assert len(s.fake.inserts)==1 and count(s,'query_governance_usage')==1


def test_stale_publication_between_preflight_and_admission_is_rejected(api_case,monkeypatch):
    s=api_case;estimate=s.fake.estimate
    def advance(query):
        change(s,'publication')
        return estimate(query)
    monkeypatch.setattr(s.fake,'estimate',advance)
    reply=post(s,model_id=s.f.model,sql=s.sql,idempotency_key='changed_head_00001')
    assert reply.status_code==409,reply.text
    assert count(s,'warehouse_query_api_requests')==0 and not s.fake.inserts


def test_original_publication_and_budget_cannot_be_replaced_before_execution(api_case):
    s=api_case;h=admit(s);s.fake.estimate_bytes=11*1024*1024
    assert post(s,'/'+h+'/execute').status_code==409
    assert count(s,'warehouse_query_dispatch_checkpoints')==0 and not s.fake.inserts
    s.fake.estimate_bytes=10*1024*1024;change(s,'publication')
    assert post(s,'/'+h+'/execute').status_code in (400,409)
    assert count(s,'warehouse_query_send_intents')==0 and not s.fake.inserts


def test_crash_after_boundary_before_send_is_lookup_only(api_case,monkeypatch):
    s=api_case;h=admit(s)
    def crash(**kwargs):raise RuntimeError('Synthetic crash after write-ahead marker')
    with monkeypatch.context() as patch:
        patch.setattr(workflow,'submit_exact_job_attempt_after_boundary',crash)
        assert post(s,'/'+h+'/execute').status_code==503
    assert not s.fake.inserts
    with s.f.pg['connect']() as conn:
        assert conn.execute("SELECT count(*) FROM warehouse_query_send_journal WHERE event='SEND_BOUNDARY'").fetchone()[0]==1
    wait_lease(s)
    reply=post(s,'/'+h+'/execute');assert reply.status_code==200,reply.text
    assert reply.json()['status']=='UNCERTAIN' and not s.fake.inserts
    assert len(s.fake.lookups)==1 and count(s,'query_governance_usage')==0


def test_lease_expiry_during_provider_io_fences_observation_then_recovers(api_case):
    s=api_case;h=admit(s);s.fake.before_lookup=lambda:wait_lease(s)
    assert post(s,'/'+h+'/execute').status_code==409
    assert count(s,'warehouse_query_recovery_attestations')==0 and count(s,'query_governance_usage')==0
    s.fake.before_lookup=None
    reply=post(s,'/'+h+'/refresh');assert reply.status_code==200,reply.text
    assert reply.json()['status']=='SUCCEEDED' and len(s.fake.inserts)==1


def test_admission_database_failure_rolls_back_attempt_draft_and_reservation(api_case,monkeypatch):
    s=api_case;baseline=count(s,'warehouse_query_attempt_registry');original=workflow.drafts._register_in_transaction
    def crash(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('Synthetic interrupted admission')
    with monkeypatch.context() as patch:
        patch.setattr(workflow.drafts,'_register_in_transaction',crash)
        reply=post(s,model_id=s.f.model,sql=s.sql,idempotency_key='rollback_request_01')
        assert reply.status_code==503,reply.text
    assert count(s,'warehouse_query_attempt_registry')==baseline
    assert count(s,'warehouse_query_api_requests')==0
    h=admit(s,'rollback_request_01')
    with s.f.pg['connect']() as conn:
        with pytest.raises(DatabaseError,match='immutable'):
            conn.execute('DELETE FROM warehouse_query_api_requests')
    assert s.client.get(s.base+'/'+h).json()['status']=='ADMITTED'


def test_concurrent_admission_reuses_one_committed_request(api_case,monkeypatch):
    from threading import Lock
    s=api_case;entered,release=Event(),Event();estimate=s.fake.estimate;guard=Lock();calls=[]
    def preflight(query):
        with guard:
            first_call=not calls;calls.append(True)
        if first_call:
            entered.set()
            if not release.wait(20):raise TimeoutError('test release missing')
        return estimate(query)
    monkeypatch.setattr(s.fake,'estimate',preflight)
    body=dict(model_id=s.f.model,sql=s.sql,idempotency_key='concurrent_admit_01')
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='admission') as pool:
        with s.client_factory() as first,s.client_factory() as second:
            future=pool.submit(first.post,s.base,json=body)
            try:
                assert entered.wait(10)
                rival=second.post(s.base,json=body);assert rival.status_code==201,rival.text
            finally:release.set()
            original=future.result(timeout=20);assert original.status_code==201,original.text
    assert original.json()['attempt_handle']==rival.json()['attempt_handle']
    assert count(s,'warehouse_query_api_requests')==1 and not s.fake.inserts


def test_provider_byte_cap_is_enforced_before_admission(api_case,monkeypatch):
    from dataclasses import replace
    s=api_case;current=policy._POLICIES[s.f.pg['workspace']]
    monkeypatch.setattr(policy,'_POLICIES',MappingProxyType({s.f.pg['workspace']:replace(current,maximum_bytes=1024)}))
    reply=post(s,model_id=s.f.model,sql=s.sql,idempotency_key='provider_budget_01')
    assert reply.status_code==409,reply.text
    assert count(s,'warehouse_query_api_requests')==0 and not s.fake.inserts
