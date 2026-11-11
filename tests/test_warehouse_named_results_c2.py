"""C2 named results through real session API and isolated SQL, provider fakes."""
from copy import deepcopy
from dataclasses import replace,asdict
from types import MappingProxyType
import pytest
from google.api_core.exceptions import NotFound
from app.services import warehouse_query_workflow_service as workflow
from app.services import warehouse_sql_provider_policy_service as policy
from app.services import warehouse_sql_recovery_attestation_service as attest
from app.warehouse.query_recovery_attestation import LookupOnlyClient
from app.warehouse.named_query_results import NamedResultPolicy, result_identity, validate_fixed_schema
from tests.test_warehouse_query_workflow_api import (
    pg,modeling_pg,fact_provider_pg,genuinely_served_query,api_case,ROOT,post,admit,count,wait_lease,
)

READER='result-reader@synthetic-project.iam.gserviceaccount.com'

class Metadata:
    project='synthetic-project';location='asia-south1';principal='attester@synthetic-project.iam.gserviceaccount.com'
    def __init__(self,fake):
        self.fake=fake;self.default_ttl=None;self.table_expiry=None;self.etag='generation-1';self.rows=2;self.bytes=32;self.wrong=False
    def get_job(self,*args,**kwargs):return self.fake.get_job(*args,**kwargs)
    def get_dataset(self,name,**kwargs):
        p,d=name.split('.')
        return dict(datasetReference=dict(projectId=p,datasetId=d),location=self.location,
            storageBillingModel='LOGICAL',defaultTableExpirationMs=self.default_ttl)
    def get_table(self,name,**kwargs):
        for job in self.fake.jobs.values():
            dest=job['configuration']['query']['destinationTable']
            if '.'.join(dest[k] for k in ('projectId','datasetId','tableId'))==name:
                return dict(tableReference={**dest,**({'tableId':'wrong'} if self.wrong else {})},location=self.location,
                    type='TABLE',etag=self.etag,schema={'fields':[dict(name='line',type='INTEGER',mode='NULLABLE')]},
                    numRows=str(self.rows),numBytes=str(self.bytes),expirationTime=self.table_expiry)
        raise NotFound('synthetic result not created')

class Reader:
    principal=READER
    def __init__(self,fake):self.fake=fake;self.calls=[]
    def get_query_results_once(self,**kwargs):
        self.calls.append(kwargs)
        if self.fake.before_results:self.fake.before_results()
        return dict(jobReference=dict(projectId=kwargs['project'],location=kwargs['location'],jobId=kwargs['job_id']),
            jobComplete=True,schema={'fields':[dict(name='line',type='INTEGER',mode='NULLABLE')]},
            rows=[dict(f=[dict(v='1')]),dict(f=[dict(v='2')])],totalRows='2')

@pytest.fixture
def named(api_case,monkeypatch):
    s=api_case
    with s.f.pg['connect']() as conn:conn.execute((ROOT/'migrations/0062_warehouse_named_query_results.sql').read_text())
    s.sql=f'SELECT line FROM model_{s.f.model} LIMIT 5'
    s.metadata=Metadata(s.fake);s.reader=Reader(s.fake)
    monkeypatch.setattr(policy,'_RESULT_POLICIES',MappingProxyType({s.f.pg['workspace']:NamedResultPolicy(READER,'named-test-1')}))
    current=attest._RUNTIMES[(s.fake.project,s.fake.location)]
    monkeypatch.setattr(attest,'_RUNTIMES',MappingProxyType({(s.fake.project,s.fake.location):replace(current,transport=LookupOnlyClient(s.metadata))}))
    s.runtime=replace(s.runtime,results_transport=s.reader)
    s.app.dependency_overrides[workflow.get_query_runtime]=lambda:s.runtime
    return s


def test_named_lifecycle_original_session_versioned_proof_and_no_duplicate(named):
    s=named;h=admit(s)
    done=post(s,'/'+h+'/execute');assert done.status_code==200,done.text
    assert done.json()['status']=='SUCCEEDED'
    assert post(s,'/'+h+'/execute').json()['status']=='SUCCEEDED' and len(s.fake.inserts)==1
    page=s.client.get(s.base+'/'+h+'/results');assert page.status_code==200,page.text
    assert page.json()['rows']==[[1],[2]] and page.json()['columns']==['line']
    assert 'dr_results_' not in page.text and 'result-reader@' not in page.text
    with s.f.pg['connect']() as conn:
        expected=conn.execute('SELECT expected FROM warehouse_query_provider_policies').fetchone()[0]
        assert expected['contract_version']==2
        dest=s.fake.inserts[0]['configuration']['query']['destinationTable']
        assert dest==expected['result']['destination'] and dest['datasetId']!=expected['dataset']
        assert s.fake.inserts[0]['configuration']['query']['writeDisposition']=='WRITE_EMPTY'
        assert conn.execute('SELECT contract_version FROM warehouse_query_recovery_attestations').fetchone()[0]==2
        assert conn.execute('SELECT count(*) FROM warehouse_query_result_attestations').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM warehouse_query_result_contracts').fetchone()[0]==1
    assert count(s,'query_governance_usage')==1


@pytest.mark.parametrize('kind',['destination','size','rows','table_expiry'])
def test_untrusted_or_oversized_result_never_settles_or_returns_rows(named,kind):
    s=named
    if kind=='destination':s.metadata.wrong=True
    if kind=='size':s.metadata.bytes=8388609
    if kind=='rows':s.metadata.rows=501
    if kind=='table_expiry':s.metadata.table_expiry='9999999999999'
    h=admit(s);reply=post(s,'/'+h+'/execute');assert reply.status_code==200,reply.text
    assert reply.json()['status']=='UNCERTAIN' and count(s,'query_governance_usage')==0
    assert s.client.get(s.base+'/'+h+'/results').status_code==409 and not s.reader.calls


def test_default_dataset_expiration_and_unbounded_string_are_refused_before_admission(named):
    s=named;s.metadata.default_ttl='60000'
    assert post(s,model_id=s.f.model,sql=s.sql,idempotency_key='ttl-rejected-00001').status_code in (400,503)
    s.metadata.default_ttl=None;s.sql=f'SELECT id FROM model_{s.f.model} LIMIT 5'
    assert post(s,model_id=s.f.model,sql=s.sql,idempotency_key='string-rejected-001').status_code==400
    assert count(s,'warehouse_query_api_requests')==0 and not s.fake.inserts


def test_independent_reader_and_post_attestation_generation_are_required(named):
    s=named;h=admit(s);assert post(s,'/'+h+'/execute').json()['status']=='SUCCEEDED'
    s.reader.principal='wrong@synthetic-project.iam.gserviceaccount.com'
    assert s.client.get(s.base+'/'+h+'/results').status_code==400 and not s.reader.calls
    s.reader.principal=READER;s.metadata.etag='modified-after-proof'
    assert s.client.get(s.base+'/'+h+'/results').status_code in (400,503) and not s.reader.calls


def test_named_concurrency_reuses_existing_claim_and_send_protocol(named):
    from tests.test_warehouse_query_workflow_api import test_concurrent_execute_calls_overlap_but_submit_once
    test_concurrent_execute_calls_overlap_but_submit_once(named)
    assert count(named,'warehouse_query_result_attestations')==1


def test_read_window_expiry_never_releases_result_or_publication_hold(named,monkeypatch):
    s=named;profile=policy._RESULT_POLICIES[s.f.pg['workspace']]
    monkeypatch.setattr(policy,'_RESULT_POLICIES',MappingProxyType({s.f.pg['workspace']:replace(profile,read_seconds=10)}))
    h=admit(s);assert post(s,'/'+h+'/execute').json()['status']=='SUCCEEDED'
    with s.f.pg['connect']() as conn:
        conn.execute("SELECT pg_sleep(LEAST(11,GREATEST(0,extract(epoch FROM max(read_valid_until)-clock_timestamp()))+0.02)) FROM warehouse_query_result_contracts")
        assert conn.execute('SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id'])).fetchone()[0]
        assert conn.execute('SELECT count(*) FROM warehouse_query_result_contracts').fetchone()[0]==1
    assert s.client.get(s.base+'/'+h).json()['status']=='EXPIRED'
    assert s.client.get(s.base+'/'+h+'/results').status_code==409
    assert not s.reader.calls and count(s,'query_governance_usage')==1


def test_deferred_named_proof_invariant_rolls_back_then_recovers_without_resend(named,monkeypatch):
    s=named;h=admit(s);original=attest._persist
    def force_commit_check(conn,*args,**kwargs):
        original(conn,*args,**kwargs)
        conn.execute('SET CONSTRAINTS warehouse_named_proof_complete IMMEDIATE')
    with monkeypatch.context() as patch:
        patch.setattr(attest,'_persist',force_commit_check)
        assert post(s,'/'+h+'/execute').status_code==503
    assert count(s,'warehouse_query_recovery_attestations')==0 and count(s,'query_governance_usage')==0
    wait_lease(s)
    reply=post(s,'/'+h+'/refresh');assert reply.status_code==200,reply.text
    assert reply.json()['status']=='SUCCEEDED' and len(s.fake.inserts)==1


@pytest.mark.parametrize('kind',['source_destination','missing_billing','lost_response'])
def test_versioned_uncertain_send_billing_and_destination_are_never_reissued(named,kind):
    s=named
    if kind=='missing_billing':s.fake.missing_billing=True
    if kind=='lost_response':s.fake.lose_insert=True
    if kind=='source_destination':
        def tamper():
            next(iter(s.fake.jobs.values()))['configuration']['query']['destinationTable']['datasetId']='dr_dev_w'+str(s.f.pg['workspace'])
        s.fake.before_lookup=tamper
    h=admit(s);reply=post(s,'/'+h+'/execute');assert reply.status_code==200,reply.text
    expected='SUCCEEDED' if kind=='lost_response' else 'UNCERTAIN'
    assert reply.json()['status']==expected
    if expected=='UNCERTAIN':
        wait_lease(s);assert post(s,'/'+h+'/execute').json()['status']=='UNCERTAIN'
        assert count(s,'query_governance_usage')==0
    assert len(s.fake.inserts)==1


@pytest.mark.parametrize('kind',['etag','session'])
def test_named_result_rechecks_metadata_and_session_after_read(named,kind):
    s=named;h=admit(s);assert post(s,'/'+h+'/execute').json()['status']=='SUCCEEDED'
    def change():
        if kind=='etag':s.metadata.etag='changed-during-read'
        else:
            with s.f.pg['connect']() as conn:conn.execute('UPDATE auth_sessions SET revoked_at=clock_timestamp()')
    s.fake.before_results=change
    response=s.client.get(s.base+'/'+h+'/results')
    assert response.status_code in (400,401,403,503) and '[[1],[2]]' not in response.text
    assert len(s.reader.calls)==1


def test_storage_admission_cap_is_atomic_and_never_released_by_settlement(named):
    s=named
    for index in range(8):
        h=admit(s,f'retained-storage-{index:04}')
        reply=post(s,'/'+h+'/execute');assert reply.status_code==200,reply.text
        assert reply.json()['status']=='SUCCEEDED'
    before=count(s,'warehouse_query_attempt_registry')
    reply=post(s,model_id=s.f.model,sql=s.sql,idempotency_key='over-cap-request-01')
    assert reply.status_code==409,reply.text
    assert count(s,'warehouse_query_result_contracts')==8
    assert count(s,'warehouse_query_attempt_registry')==before
    assert len(s.fake.inserts)==8 and count(s,'query_governance_usage')==8


def test_connection_factory_keeps_outer_transaction_rollback_and_rejects_cross_duty(named):
    from app.db import database
    from app.db import warehouse_query_send_repository as send
    s=named;created=[]
    def factory():
        conn=s.f.pg['connect']();created.append(conn);return conn
    with s.f.pg['connect']() as conn:conn.execute('CREATE TABLE c2_boundary_probe(value INTEGER)')
    with database.connection_boundary('query_control',factory):
        with pytest.raises(RuntimeError,match='Cross-duty'):
            with send.transaction(duty='accounting'):pass
        assert not created
        with send.transaction(s.f.pg['workspace']) as outer:
            with database.get_connection() as borrowed:
                borrowed.execute('INSERT INTO c2_boundary_probe VALUES(1)');borrowed.commit()
                assert borrowed.connection is outer
            with pytest.raises(RuntimeError,match='cannot nest'):
                with database.connection_boundary('accounting',factory):pass
        with pytest.raises(ValueError,match='rollback'):
            with send.transaction(s.f.pg['workspace']) as conn:
                conn.execute('INSERT INTO c2_boundary_probe VALUES(2)');raise ValueError('rollback')
    assert len(created)==2 and all(c.closed for c in created)
    with s.f.pg['connect']() as conn:assert conn.execute('SELECT value FROM c2_boundary_probe').fetchall()==[(1,)]


def test_result_contract_and_proof_are_immutable_and_cannot_downgrade(named):
    from psycopg import DatabaseError
    s=named;h=admit(s);assert post(s,'/'+h+'/execute').json()['status']=='SUCCEEDED'
    for table in ['warehouse_query_result_contracts','warehouse_query_result_attestations']:
        with s.f.pg['connect']() as conn:
            with pytest.raises(DatabaseError,match='immutable'):
                conn.execute('DELETE FROM '+table)
    with s.f.pg['connect']() as conn:
        with pytest.raises(DatabaseError):
            conn.execute('UPDATE warehouse_query_provider_policies SET expected=jsonb_set(expected,\'{contract_version}\',\'1\')')
