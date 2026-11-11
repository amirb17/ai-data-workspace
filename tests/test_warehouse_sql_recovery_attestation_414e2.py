"""414E2 actual PG + fake jobs.get metadata; never analytical query/result IO."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Event,Lock
from types import MappingProxyType,SimpleNamespace
from uuid import uuid4
import os

from google.api_core.exceptions import NotFound,Forbidden,TooManyRequests,ServiceUnavailable
from google.cloud.bigquery.job import QueryJob
import psycopg
import pytest

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_send_recovery_service as recovery
from app.services import warehouse_sql_recovery_attestation_service as adapter
from app.warehouse.query_recovery_attestation import LookupPolicy,LookupOnlyClient,LookupRuntime
from tests.test_warehouse_sql_send_recovery_414e1 import (
    pg,modeling_pg,fact_provider_pg,genuinely_served_query,served,checkpointed,prepared,
    prepare,view,claim,observe,wait_lease,scalar,state,change,
)

ROOT=Path(__file__).resolve().parents[1]
CREATOR='synthetic-query@synthetic-project.iam.gserviceaccount.com'


class FakeJob:
    def __init__(self,body):self.body=body
    @property
    def _properties(self):return deepcopy(self.body)
    def to_api_repr(self):raise AssertionError('Submission representation is incomplete')
    def result(self,*args,**kwargs):raise AssertionError('Result access forbidden')


class FakeClient:
    def __init__(self,body):self.body,self.error,self.calls,self.before=body,None,[],None
    def get_job(self,job_id,**kwargs):
        self.calls.append((job_id,kwargs))
        assert kwargs==dict(project='synthetic-project',location='asia-south1',retry=None,timeout=10)
        if self.before:self.before()
        if self.error:raise self.error
        return FakeJob(self.body)
    def query(self,*args,**kwargs):raise AssertionError('Query submission forbidden')
    def cancel_job(self,*args,**kwargs):raise AssertionError('Cancel forbidden')


@pytest.fixture
def lookup(prepared,monkeypatch,request):
    s=prepared;assert os.getenv('RUN_DB_TESTS')=='1'
    with s.f.pg['connect']() as conn:
        conn.execute((ROOT/'migrations/0055_warehouse_query_recovery_attestations.sql').read_text())
        started=conn.execute('SELECT execution_started_at FROM query_governance_reservations WHERE reservation_id=%s',(s.r,)).fetchone()[0]
    lease=claim(s,getattr(request,"param",30))
    project,dataset,physical=s.q.physical_tables[0].split('.')
    moment=int(started.timestamp()*1000)
    body=dict(kind='bigquery#job',user_email=CREATOR,
        jobReference=dict(projectId=lease.job.project,location=lease.job.location,jobId=lease.job.job_id),
        configuration=dict(jobType='QUERY',dryRun=False,jobTimeoutMs='60000',labels=dict(datarise='query',workspace=str(s.f.pg['workspace']),
            reservation=str(s.r),intent=s.q.intent_sha256[:63]),query=dict(query=s.q.sql,useLegacySql=False,useQueryCache=False,
                priority='INTERACTIVE',maximumBytesBilled=str(10*1024*1024))),
        statistics=dict(creationTime=str(moment),startTime=str(moment+1),endTime=str(moment+2),query=dict(statementType='SELECT',
            referencedTables=[dict(projectId=project,datasetId=dataset,tableId=physical)],cacheHit=False,
            totalBytesProcessed='1024',totalBytesBilled='10485760')),
        status=dict(state='DONE'))
    client=FakeClient(body);policy=LookupPolicy('synthetic-project','asia-south1',CREATOR,'test-reviewed-1')
    runtime=LookupRuntime(policy,LookupOnlyClient(client))
    monkeypatch.setattr(adapter,'_RUNTIMES',MappingProxyType({('synthetic-project','asia-south1'):runtime}))
    return s,lease,client


def run(lease,revision=None):return adapter.lookup_and_attest(lease=lease,expected_revision=lease.revision if revision is None else revision)


@pytest.mark.parametrize('outcome',['PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED'])
def test_independent_metadata_outcomes_do_not_settle_or_fetch_results(lookup,outcome):
    s,lease,client=lookup
    if outcome in ('PENDING','RUNNING'):
        client.body['status']['state']=outcome;client.body['statistics'].pop('endTime')
        client.body['statistics']['query'].pop('totalBytesProcessed');client.body['statistics']['query'].pop('totalBytesBilled')
    elif outcome!='SUCCEEDED':
        client.body['status']['errorResult']=dict(reason='stopped' if outcome=='CANCELLED' else 'invalidQuery')
    result=run(lease)
    assert result.verdict=='VERIFIED_METADATA' and result.observation==('IN_PROGRESS' if outcome in ('PENDING','RUNNING') else outcome)
    assert not result.execution_authorized and not result.provider_dispatched
    assert len(client.calls)==1 and state(s)=='EXECUTING'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations')==1
    assert scalar(s,'SELECT count(*) FROM query_governance_usage')==0
    assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))


@pytest.mark.parametrize('error',[NotFound('not found'),Forbidden('not allowed'),TooManyRequests('busy'),ServiceUnavailable('backend'),TimeoutError('lost response')])
def test_missing_and_transport_errors_are_held_not_proof_of_nonexecution(lookup,error):
    s,lease,client=lookup;client.error=error
    result=run(lease)
    assert result.observation==('NOT_FOUND' if isinstance(error,NotFound) else 'UNCERTAIN')
    assert result.verdict==('ABSENT' if isinstance(error,NotFound) else 'UNCERTAIN')
    assert view(s).phase=='INTENT_RECORDED' and state(s)=='EXECUTING'
    assert scalar(s,'SELECT count(*) FROM query_governance_usage')==0


@pytest.mark.parametrize('bad',['job','project','location','creator','labels','sql','legacy','cache','cap','timeout','refs',
                               'routines','script','destination','missing_creator','missing_stats','missing_billing','errors','unknown_state','negative_bill','timestamp'])
def test_forged_or_insufficient_response_cannot_create_verified_terminal_event(lookup,bad):
    s,lease,client=lookup;b=client.body;q=b['configuration']['query'];stats=b['statistics']['query']
    if bad=='job':b['jobReference']['jobId']='wrong-job'
    elif bad=='project':b['jobReference']['projectId']='foreign-project'
    elif bad=='location':b['jobReference']['location']='US'
    elif bad=='creator':b['user_email']='foreign-query@foreign-project.iam.gserviceaccount.com'
    elif bad=='labels':b['configuration']['labels']['reservation']='999'
    elif bad=='sql':q['query']+=' LIMIT 999'
    elif bad=='legacy':q['useLegacySql']=True
    elif bad=='cache':q['useQueryCache']=True
    elif bad=='cap':q['maximumBytesBilled']=str(1<<30)
    elif bad=='timeout':b['configuration']['jobTimeoutMs']='300000'
    elif bad=='refs':stats['referencedTables'][0]['datasetId']='foreign_dataset'
    elif bad=='routines':stats['referencedRoutines']=[dict(routineId='unreviewed')]
    elif bad=='script':b['statistics']['scriptStatistics']={ 'evaluationKind':'STATEMENT' }
    elif bad=='destination':q['destinationTable']=dict(projectId='synthetic-project',datasetId='_anonymous',tableId='unknown')
    elif bad=='missing_creator':b.pop('user_email')
    elif bad=='missing_stats':stats.pop('referencedTables')
    elif bad=='missing_billing':stats.pop('totalBytesBilled')
    elif bad=='errors':b['status']['errors']=[dict(reason='ambiguous')]
    elif bad=='unknown_state':b['status']['state']='CANCELLED'
    elif bad=='negative_bill':stats['totalBytesBilled']='-1'
    elif bad=='timestamp':b['statistics']['creationTime']='1'
    result=run(lease)
    assert result.verdict in ('REJECTED','UNCERTAIN') and result.observation=='UNCERTAIN'
    assert view(s).phase=='INTENT_RECORDED'
    assert scalar(s,"SELECT count(*) FROM warehouse_query_recovery_attestations WHERE verdict='VERIFIED_METADATA'")==0


def test_failure_without_billing_explicitly_keeps_billing_incomplete(lookup):
    s,lease,client=lookup
    client.body['status']['errorResult']=dict(reason='stopped')
    client.body['statistics']['query'].pop('totalBytesProcessed');client.body['statistics']['query'].pop('totalBytesBilled')
    result=run(lease)
    assert result.observation=='CANCELLED' and result.verdict=='VERIFIED_METADATA' and not result.billing_complete
    assert state(s)=='EXECUTING' and scalar(s,'SELECT count(*) FROM query_governance_usage')==0


def test_unconfigured_runtime_and_caller_provider_or_proof_fail_closed(lookup,monkeypatch):
    s,lease,client=lookup
    for field in ('provider','client','sql','permit','response','verified','policy'):
        with pytest.raises(TypeError):adapter.lookup_and_attest(lease=lease,expected_revision=lease.revision,**{field:True})
    with monkeypatch.context() as patch:
        patch.setattr(adapter,'_RUNTIMES',MappingProxyType({}))
        with pytest.raises(adapter.LookupUnavailable):run(lease)
    assert not client.calls and view(s).revision==lease.revision


def test_stale_token_or_revision_never_starts_provider_lookup(lookup):
    s,lease,client=lookup
    with pytest.raises(repo.RecoveryConflict):run(replace(lease,token=uuid4()))
    with pytest.raises(repo.RecoveryConflict):run(lease,lease.revision-1)
    assert not client.calls


def test_provider_response_loss_and_commit_response_loss_never_resubmit(lookup):
    s,lease,client=lookup;client.error=TimeoutError('sensitive text must not persist')
    uncertain=run(lease)
    assert uncertain.observation=='UNCERTAIN' and 'sensitive' not in uncertain.reason
    client.error=None;verified=run(lease,uncertain.revision)
    assert verified.verdict=='VERIFIED_METADATA'
    with pytest.raises(repo.RecoveryConflict):run(lease,uncertain.revision)
    assert len(client.calls)==2 and scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations')==2


def test_journal_and_attestation_rollback_together(lookup,monkeypatch):
    s,lease,client=lookup;persist=adapter._persist
    def crash(*args,**kwargs):
        persist(*args,**kwargs);raise RuntimeError('Synthetic after-attestation precommit failure')
    with monkeypatch.context() as patch:
        patch.setattr(adapter,'_persist',crash)
        with pytest.raises(RuntimeError,match='precommit'):run(lease)
    assert view(s).revision==lease.revision and scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations')==0
    assert run(lease).verdict=='VERIFIED_METADATA'


@pytest.mark.parametrize('kind',['account','publication'])
def test_revocation_during_lookup_keeps_historic_attestation_and_no_database_locks(lookup,kind):
    s,lease,client=lookup;entered,release=Event(),Event()
    def pause():
        entered.set()
        if not release.wait(30):raise TimeoutError('Test release missing')
    client.before=pause
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(run,lease)
        try:
            assert entered.wait(10)
            with s.f.pg['connect']() as conn:
                assert conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',('datarise-query-governance-global',)).fetchone()[0]
                conn.execute('SELECT attempt_id FROM warehouse_query_send_intents WHERE attempt_id=%s FOR UPDATE NOWAIT',(s.a.attempt_id,)).fetchone()
            change(s,kind)
        finally:release.set()
        result=future.result(timeout=30)
    assert result.verdict=='VERIFIED_METADATA' and result.recovery.revoked
    assert result.recovery.classification=='REVOKED_HELD' and not result.execution_authorized


def test_contradictory_terminal_observation_is_rejected_without_attestation(lookup):
    s,lease,client=lookup
    prior=observe(lease,lease.revision,recovery.Observation.SUCCEEDED)
    client.body['status']['errorResult']=dict(reason='stopped')
    with pytest.raises(psycopg.DatabaseError,match='contradict'):
        run(lease,prior.revision)
    assert scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations')==0
    assert view(s).phase=='SUCCEEDED_OBSERVED'


def test_attestation_is_immutable_and_linked_to_observation(lookup):
    s,lease,client=lookup;run(lease)
    with s.f.pg['connect']() as conn:
        for sql in ('DELETE FROM warehouse_query_recovery_attestations',"UPDATE warehouse_query_recovery_attestations SET reason='FORGED'",
                    'TRUNCATE warehouse_query_recovery_attestations'):
            with pytest.raises(psycopg.DatabaseError):
                with conn.transaction():conn.execute(sql)
        with pytest.raises(psycopg.errors.DuplicateTable):
            with conn.transaction():conn.execute((ROOT/'migrations/0055_warehouse_query_recovery_attestations.sql').read_text())



@pytest.mark.parametrize('lookup',[1],indirect=True)
def test_expired_then_reclaimed_owner_cannot_persist_valid_provider_response(lookup):
    s,lease,client=lookup;entered,release=Event(),Event()
    def pause():
        entered.set()
        if not release.wait(30):raise TimeoutError('Test release missing')
    client.before=pause
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(run,lease)
        try:
            assert entered.wait(10)
            wait_lease(s,lease)
            newer=recovery.acquire_recovery(attempt_id=s.a.attempt_id,expected_revision=lease.revision)
            assert newer.token!=lease.token and newer.generation==lease.generation+1
        finally:release.set()
        with pytest.raises(repo.RecoveryConflict,match='STALE'):future.result(timeout=30)
    assert scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations')==0
    client.before=None
    assert run(newer).verdict=='VERIFIED_METADATA'


def test_duplicate_inflight_lookups_cannot_both_commit_same_revision(lookup):
    s,lease,client=lookup;both,release=Event(),Event();counter=[0];mutex=Lock()
    def rendezvous():
        with mutex:
            counter[0]+=1
            if counter[0]==2:both.set()
        if not release.wait(30):raise TimeoutError('Duplicate lookup release missing')
    client.before=rendezvous
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,lease) for _ in range(2)]
        try:assert both.wait(10)
        finally:release.set()
        successes=[];conflicts=[]
        for future in futures:
            try:successes.append(future.result(timeout=30))
            except repo.RecoveryConflict as error:conflicts.append(str(error))
    assert len(successes)==1 and conflicts==['STALE_RECOVERY_REVISION_INSPECT_ONLY']
    assert scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations')==1
    assert view(s).revision==lease.revision+1 and len(client.calls)==2


def test_not_found_after_metadata_conversion_is_uncertain_not_absent(lookup,monkeypatch):
    s,lease,client=lookup
    def malformed(_):raise NotFound('conversion failure, not jobs.get absence')
    monkeypatch.setattr(FakeJob,'_properties',property(malformed))
    result=run(lease)
    assert result.observation=='UNCERTAIN' and result.verdict=='UNCERTAIN'


def test_oversized_metadata_is_not_verified_or_persisted_as_payload(lookup):
    s,lease,client=lookup;client.body['padding']='x'*(256*1024)
    result=run(lease)
    assert result.observation=='UNCERTAIN' and result.verdict=='UNCERTAIN'
    assert scalar(s,'SELECT payload_sha256 IS NULL FROM warehouse_query_recovery_attestations')


def test_configured_principal_subject_must_be_present_and_exact(lookup,monkeypatch):
    s,lease,client=lookup
    policy=LookupPolicy('synthetic-project','asia-south1',CREATOR,'review-subject-1',principal_subject='serviceAccount:'+CREATOR)
    monkeypatch.setattr(adapter,'_RUNTIMES',MappingProxyType({('synthetic-project','asia-south1'):LookupRuntime(policy,LookupOnlyClient(client))}))
    missing=run(lease)
    assert missing.verdict=='UNCERTAIN' and missing.reason=='MISSING_CREATOR'
    client.body['principal_subject']='foreign-principal'
    wrong=run(lease,missing.revision)
    assert wrong.verdict=='REJECTED'
    client.body['principal_subject']='serviceAccount:'+CREATOR
    assert run(lease,wrong.revision).verdict=='VERIFIED_METADATA'


def test_verified_cancellation_survives_missing_lookup_without_release(lookup):
    s,lease,client=lookup
    client.body['status']['errorResult']=dict(reason='stopped')
    cancelled=run(lease)
    assert cancelled.observation=='CANCELLED' and cancelled.verdict=='VERIFIED_METADATA'
    client.error=NotFound('provider history unavailable')
    missing=run(lease,cancelled.revision)
    assert missing.observation=='NOT_FOUND' and missing.recovery.phase=='CANCELLED_OBSERVED'
    assert missing.recovery.classification=='TERMINAL_OBSERVED_HELD'
    assert state(s)=='EXECUTING' and scalar(s,'SELECT count(*) FROM query_governance_usage')==0
    assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))


def test_attestation_cannot_attach_to_lease_or_invented_journal_revision(lookup):
    s,lease,client=lookup
    with s.f.pg['connect']() as conn:
        for revision in (lease.revision,lease.revision+100):
            with pytest.raises(psycopg.DatabaseError,match='Matching fenced journal'):
                with conn.transaction():
                    conn.execute("""INSERT INTO warehouse_query_recovery_attestations
                        (attempt_id,journal_revision,observation,verdict,reason,policy_sha256,billing_complete)
                        VALUES(%s,%s,'NOT_FOUND','ABSENT','LOOKUP_NOT_FOUND',%s,false)""",
                        (s.a.attempt_id,revision,'a'*64))
    assert scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations')==0
    assert view(s).revision==lease.revision


def test_actual_sdk_job_retains_full_metadata_not_submission_representation(lookup,monkeypatch):
    s,lease,client=lookup
    original=client.get_job
    def hydrate(*args,**kwargs):
        raw=original(*args,**kwargs)._properties
        job=QueryJob.from_api_repr(raw,client=SimpleNamespace(project='synthetic-project'))
        assert 'status' not in job.to_api_repr() and 'user_email' not in job.to_api_repr()
        assert type(job._properties['statistics']['creationTime']) is float
        return job
    monkeypatch.setattr(client,'get_job',hydrate)
    result=run(lease)
    assert result.verdict=='VERIFIED_METADATA' and result.observation=='SUCCEEDED'
    assert result.billing_complete and state(s)=='EXECUTING'


@pytest.mark.parametrize('value',[1.5,float(2**53)])
def test_inexact_or_unsafe_sdk_timestamp_fails_closed(lookup,value):
    s,lease,client=lookup
    client.body['statistics']['creationTime']=value
    result=run(lease)
    assert result.verdict=='UNCERTAIN' and result.observation=='UNCERTAIN'
    assert view(s).phase=='INTENT_RECORDED'
