"""Offline transport/admission/versioned-ingestion wiring; no SDK construction."""
import hashlib
import json
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.lambda_worker import admission
from app.processing_worker import execution
from app.processing_worker.sqs_transport import SqsTransport
from app.processing_worker.worker import SerialWorker
from app.storage.versioned_artifacts import MemoryVersionedClient, StorageIntegrityError
from app.storage.versioned_ingestion import read_staged,write_raw


class Client:
    def __init__(self,body):
        self.body=body; self.calls=[]; self.closed=False
    def receive_message(self,**kwargs):
        assert kwargs['MaxNumberOfMessages']==1
        return {'Messages':[dict(MessageId='one',Body=self.body,ReceiptHandle='private-receipt')]}
    def change_message_visibility(self,**kwargs): self.calls.append(('visibility',kwargs['VisibilityTimeout']))
    def delete_message(self,**kwargs): self.calls.append(('delete',None))
    def close(self): self.closed=True


@pytest.mark.parametrize('failure',[None,'execution','completion','malformed'])
def test_sqs_delete_only_after_shared_execution_returns(monkeypatch,failure):
    stop=Event(); body=json.dumps({'job_id':str(uuid4())}) if failure!='malformed' else '{}'
    client=Client(body); transport=SqsTransport(client,'offline',enabled=True)
    def execute(job_id,**kwargs):
        kwargs['lease_observer'](SimpleNamespace())
        client.calls.append(('durable-completion' if failure is None else failure,None))
        stop.set()
        if failure: raise RuntimeError('no durable completion')
    monkeypatch.setattr(execution,'execute_job',execute)
    original_retry=transport.retry
    def retry(message): stop.set(); original_retry(message)
    transport.retry=retry
    SerialWorker(transport,stop=stop,boundary_executor=lambda b:None).run()
    assert client.closed
    if failure is None:
        assert client.calls==[('visibility',300),('durable-completion',None),('delete',None)]
    else:
        assert not any(c[0]=='delete' for c in client.calls)
        assert client.calls[-1]==('visibility',30)


def test_transport_and_admission_default_disabled():
    with pytest.raises(RuntimeError): SqsTransport(object(),'offline')
    with pytest.raises(RuntimeError): admission.admit_event({},authenticate=lambda e:None)


@pytest.mark.parametrize('body',[{},[],{'upload_id':1,'operation':'BRONZE','idempotency_key':'x','user_id':123},
    {'upload_id':True,'operation':'BRONZE','idempotency_key':'x'},
    {'upload_id':1,'operation':'CONTINUE','idempotency_key':'x'},
    {'upload_id':1,'operation':'BRONZE','idempotency_key':'   '}])
def test_lambda_admission_rejects_client_scope(monkeypatch,body):
    monkeypatch.setattr(admission,'admit',lambda *a:pytest.fail('invalid request reached DB'))
    with pytest.raises(ValueError): admission.admit_event({'body':json.dumps(body)},authenticate=lambda e:{'user_id':7},enabled=True)


def test_lambda_admission_uses_authenticated_actor(monkeypatch):
    job=uuid4(); seen=[]
    def admit(*args): seen.append(args); return job
    monkeypatch.setattr(admission,'admit',admit)
    body=dict(upload_id=4,operation='BRONZE',idempotency_key='intent')
    assert admission.admit_event({'body':json.dumps(body)},authenticate=lambda e:{'user_id':7},enabled=True)=={'job_id':str(job)}
    assert seen==[(7,4,'BRONZE','intent')]


def test_versioned_ingestion_does_not_overwrite_historical_raw():
    client=MemoryVersionedClient(); payload=b'id\n1\n'; source=client.seed('staging/source',payload)
    body,checksum=read_staged(client,'offline',source.key,len(payload))
    first=write_raw(client,'offline',body,checksum,'source.csv')
    second=write_raw(client,'offline',body,checksum,'source.csv')
    assert first.key!=second.key and first.sha256==second.sha256==hashlib.sha256(payload).hexdigest()
    assert client.objects[first.key,first.version]==payload


@pytest.mark.parametrize('fault',['size','version','hash','filename'])
def test_ingestion_identity_failures(fault):
    client=MemoryVersionedClient(); payload=b'id\n1\n'; source=client.seed('staging/source',payload)
    with pytest.raises((StorageIntegrityError,ValueError)):
        if fault=='size': read_staged(client,'offline',source.key,len(payload)+1)
        elif fault=='version':
            head=client.head_object
            client.head_object=lambda **kw: head(**kw)|{'VersionId':'null'}
            read_staged(client,'offline',source.key,len(payload))
        else: write_raw(client,'offline',payload,'0'*64 if fault=='hash' else source.sha256,'../bad.csv' if fault=='filename' else 'source.csv')


def test_immutable_key_shape_alone_cannot_bypass_silver_lineage():
    from app.processing_worker.incremental_adapter import ApplyGuard
    from app.storage.versioned_artifacts import VersionedArtifacts
    identity=MemoryVersionedClient().seed(f'artifact-candidates/{uuid4()}/data.parquet',b'not authorized')
    contract=dict(dataset_id=1,dataset_version=2,file_id=3,association=4,rule_version=5)
    guard=ApplyGuard(None,VersionedArtifacts(object(),'offline'),contract,identity)
    with pytest.raises(ValueError,match='lineage differs'):
        guard.authorize_silver_source(identity.key,dict(dataset_version_file_id=999,
            applied_rule_version=5,source_dq_run_id=8),dict(dq_run_id=8,silver_sha256=identity.sha256),3)


def test_legacy_writer_guard_preserves_default_and_rejects_managed_source(monkeypatch):
    from app.processing_worker import io_context
    from app.db import database
    monkeypatch.delenv('DATARISE_PROCESSING_READERS',raising=False)
    monkeypatch.setattr(database,'get_connection',lambda:pytest.fail('default path must stay unchanged'))
    io_context.require_legacy_source(1)
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,query,args):
            assert query=='SELECT task20_file_managed(%s)' and args==(1,)
            return self
        def fetchone(self): return (True,)
    monkeypatch.setattr(database,'get_connection',Connection)
    monkeypatch.setenv('DATARISE_PROCESSING_READERS','true')
    with pytest.raises(RuntimeError,match='asynchronous stage job'): io_context.require_legacy_source(1)
    with io_context.using(object()): io_context.require_legacy_source(1)
