"""Offline runtime composition and failure boundaries; all credentials are synthetic."""
import io
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.processing_worker import container_entrypoint as entry
from app.processing_worker.runtime_clients import client_config
from app.processing_worker.runtime_db_secret import database_url_from_secret, load_database_url
from app.processing_worker.secure_db_connection import secure_conninfo
from app.processing_worker.verifier_client import LambdaVerifierClient, RemoteVerificationError


def secret(**overrides):
    return json.dumps(dict(username='datarise_worker',password='synthetic-only',
        host='db.example.test',port=5432,dbname='workspace',**overrides))


@pytest.mark.parametrize('document',[
    '{"username":"one","username":"two"}',
    json.dumps(dict(username='datarise_worker',password='bad\u0000value',host='db.example.test',port=5432,dbname='x')),
    json.dumps(dict(username='datarise_worker',password='x',host='db.example.test',port=5432,dbname='\u0000')),
    'x'*65537,
], ids=['duplicate-fields','nul-password','nul-database','oversized'])
def test_reject_ambiguous_or_unusable_credentials(document):
    with pytest.raises(ValueError): database_url_from_secret(document,'datarise_worker')


def test_secret_fetch_failure_does_not_expose_sdk_error():
    class Client:
        def get_secret_value(self,**kwargs): raise RuntimeError('sensitive synthetic response')
    with pytest.raises(RuntimeError) as error: load_database_url(Client(),'test-secret','datarise_worker')
    assert 'sensitive' not in ''.join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None


@pytest.mark.parametrize('response',[None,[],{}, {'SecretBinary':b'not-supported'}, {'SecretString':3}])
def test_malformed_secret_response(response):
    client=SimpleNamespace(get_secret_value=lambda **kw:response)
    with pytest.raises(ValueError):load_database_url(client,'test-secret','datarise_worker')


def test_verified_tls_and_bounded_connection_cannot_be_downgraded(tmp_path):
    ca=tmp_path/'test-ca.pem';ca.write_text('synthetic CA placeholder')
    url=database_url_from_secret(secret(),'datarise_worker')+'?sslmode=disable&connect_timeout=999'
    values=conninfo_to_dict(secure_conninfo(url,str(ca)))
    assert values['sslmode']=='verify-full' and values['gssencmode']=='disable'
    assert values['sslrootcert']==str(ca) and values['connect_timeout']=='5'
    assert values['keepalives']=='1'


@pytest.mark.parametrize('suffix',['?hostaddr=127.0.0.1','?service=other','?options=-csearch_path=public'])
def test_runtime_connection_rejects_unreviewed_overrides(tmp_path,suffix):
    ca=tmp_path/'test-ca';ca.write_text('placeholder')
    with pytest.raises(ValueError):secure_conninfo(database_url_from_secret(secret(),'datarise_worker')+suffix,str(ca))


def test_missing_ca_fails_closed(tmp_path):
    with pytest.raises(ValueError):secure_conninfo(database_url_from_secret(secret(),'datarise_worker'),str(tmp_path/'absent'))


def test_sdk_budgets_and_no_automatic_verifier_reinvocation():
    for service in ('sqs','s3','secretsmanager','lambda'):
        cfg=client_config(service)
        assert cfg.connect_timeout==3 and cfg.max_pool_connections==2
        assert cfg.read_timeout<=45
        assert cfg.retries['total_max_attempts']==(1 if service=='lambda' else 2)
    assert client_config('sqs').read_timeout>10  # The receive long poll is ten seconds.


def configure_worker(monkeypatch):
    for name,value in dict(DATARISE_PROCESSING_DEPLOYMENT_APPROVED='true',DATARISE_PROCESSING_READERS='true',
        DATARISE_PROCESSING_QUEUE_URL='https://sqs.ap-southeast-2.amazonaws.com/000000000000/test',
        DATARISE_ARTIFACT_BUCKET='synthetic-artifacts',DATARISE_VERIFIER_FUNCTION_NAME='synthetic-verifier',
        AWS_DEFAULT_REGION='ap-southeast-2').items():monkeypatch.setenv(name,value)
    monkeypatch.delenv('AWS_REGION',raising=False)
    monkeypatch.delenv('S3_BUCKET_NAME',raising=False)


@pytest.mark.parametrize('flag',['DATARISE_PROCESSING_DEPLOYMENT_APPROVED','DATARISE_PROCESSING_READERS'])
def test_container_guard_precedes_all_sdk_work(monkeypatch,flag):
    configure_worker(monkeypatch);monkeypatch.setenv(flag,'false')
    monkeypatch.setattr(entry.boto3,'Session',lambda **kw:pytest.fail('SDK constructed while disabled'))
    with pytest.raises(PermissionError):entry.main()


@pytest.mark.parametrize('failure',[None,'database','build','run'])
def test_container_composition_owns_clients_and_signal_runner(monkeypatch,caplog,failure):
    from app.processing_worker import bootstrap,worker
    configure_worker(monkeypatch)
    clients={};calls=[]
    class Client:
        closed=0
        def close(self): self.closed+=1
    class Session:
        def client(self,service,**kwargs):
            assert kwargs['config'].connect_timeout==3
            clients[service]=Client();return clients[service]
    monkeypatch.setattr(entry.boto3,'Session',lambda **kw:Session())
    def initialize(session):
        assert entry.os.environ['AWS_REGION']=='ap-southeast-2'
        assert entry.os.environ['S3_BUCKET_NAME']=='synthetic-artifacts'
        calls.append('database')
        if failure=='database':raise RuntimeError('sensitive synthetic credential error')
    monkeypatch.setattr(entry,'initialize_database',initialize)
    def build(**kwargs):
        calls.append('build')
        if failure=='build':raise RuntimeError('sensitive synthetic construction error')
        return object()
    monkeypatch.setattr(bootstrap,'build_worker',build)
    def run(instance):
        calls.append('signal-runner');clients['sqs'].close()
        if failure=='run':raise RuntimeError('sensitive synthetic execution error')
        return 0
    monkeypatch.setattr(worker,'run_worker',run)
    assert entry.cli()==(2 if failure else 0)
    assert all(c.closed==1 for c in clients.values())
    assert 'sensitive' not in caplog.text
    if failure=='database':assert clients=={} and calls==['database']
    elif failure is None:assert calls==['database','build','signal-runner']


@pytest.mark.parametrize('name,value',[('AWS_REGION','other-region'),('S3_BUCKET_NAME','different-bucket')])
def test_container_rejects_conflicting_legacy_configuration(monkeypatch,name,value):
    configure_worker(monkeypatch);monkeypatch.setenv(name,value)
    monkeypatch.setattr(entry.boto3,'Session',lambda **kw:pytest.fail('conflicting config reached SDK'))
    with pytest.raises(ValueError):entry.main()


def test_database_initialization_preflights_role_and_removes_environment_copy(monkeypatch):
    from app.processing_worker import secure_db_connection
    monkeypatch.delitem(sys.modules,'app.db.database',raising=False)
    monkeypatch.delitem(sys.modules,'app.config',raising=False)
    monkeypatch.setenv('DATARISE_WORKER_DB_SECRET_ARN','synthetic-reference')
    client=SimpleNamespace(get_secret_value=lambda **kw:{'SecretString':secret()},close=lambda:None)
    session=SimpleNamespace(client=lambda *a,**kw:client)
    monkeypatch.setattr(secure_db_connection,'secure_conninfo',lambda url,ca:'synthetic-conninfo')
    observed=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):observed.append('closed')
    db=SimpleNamespace(get_connection=Connection)
    security=SimpleNamespace(require_runtime=lambda conn,purpose:observed.append(purpose))
    def imported(name):
        if name=='app.db.database':
            assert entry.os.environ['DATABASE_URL']=='synthetic-conninfo';return db
        return security
    monkeypatch.setattr(entry.importlib,'import_module',imported)
    entry.initialize_database(session)
    assert observed==['worker','closed']
    assert 'DATABASE_URL' not in entry.os.environ


@pytest.mark.parametrize('failure',['invoke','read','invalid-type','oversized','function-error'])
def test_verifier_failures_close_payload_and_never_confirm(failure):
    candidate=uuid4();closed=[]
    class Body:
        def read(self,n):
            if failure=='read':raise RuntimeError('sensitive synthetic payload')
            if failure=='invalid-type':return None
            if failure=='oversized':return b'x'*n
            return json.dumps({'verified':True,'candidate_id':str(candidate)}).encode()
        def close(self):closed.append(True)
    def invoke(**kwargs):
        if failure=='invoke':raise RuntimeError('sensitive synthetic SDK error')
        result={'StatusCode':200,'Payload':Body()}
        if failure=='function-error':result['FunctionError']='Unhandled'
        return result
    with pytest.raises(RemoteVerificationError) as error:
        LambdaVerifierClient(SimpleNamespace(invoke=invoke),'test-verifier')(candidate)
    assert 'sensitive' not in ''.join(traceback.format_exception(error.value))
    assert closed==([] if failure=='invoke' else [True])


def test_lambda_handler_sanitizes_bootstrap_failure(monkeypatch):
    from app.lambda_worker import verifier_handler as handler
    def build():raise RuntimeError('sensitive synthetic secret data')
    monkeypatch.setattr(handler,'build_verifier',build)
    with pytest.raises(RuntimeError) as error:handler.lambda_handler({'candidate_id':str(uuid4())},None)
    assert 'sensitive' not in ''.join(traceback.format_exception(error.value))


def test_malformed_lambda_request_does_not_construct_runtime(monkeypatch):
    from app.lambda_worker import verifier_handler as handler
    monkeypatch.setattr(handler,'build_verifier',lambda:pytest.fail('malformed input reached credentials'))
    with pytest.raises(RuntimeError):handler.lambda_handler({'candidate_id':'bad'},None)


def test_verifier_deadline_precedes_credentials(monkeypatch):
    from app.lambda_worker.verifier_runtime import build_verifier
    configure_worker(monkeypatch)
    monkeypatch.setenv('DATARISE_VERIFIER_DB_SECRET_ARN','synthetic-verifier-secret')
    with pytest.raises(TimeoutError):build_verifier(remaining_time_ms=lambda:4999)


def test_deployment_defaults_and_required_runtime_imports():
    for filename in ('Dockerfile.worker','Dockerfile.verifier'):
        content=Path('deployment/docker',filename).read_text()
        assert "p['project']['dependencies']" in content
        assert 'DATARISE_PROCESSING_DEPLOYMENT_APPROVED=false' in content
        assert 'DATARISE_PROCESSING_READERS=false' in content
        assert 'OPENBLAS_NUM_THREADS=1' in content
    script=Path('deployment/terraform/environments/dev/worker-user-data.sh.tftpl').read_text()
    assert script.index('exit 78')<script.index('aws ecr get-login-password')
    for option in ('--init','--read-only','--memory=512m','--pids-limit=128','--stop-timeout=90','--log-opt=max-file=3'):
        assert option in script
    assert 'RestartPreventExitStatus=78' in script
    assert 'systemctl enable --now ai-workspace-worker' not in script
    assert 'systemctl start ai-workspace-worker' not in script
    policy=Path('deployment/terraform/environments/dev/ec2_worker_iam.tf').read_text()
    assert 'delivery/upload_request_id=*/*' in policy and 'state/application_id=*/*' in policy
    assert policy.count('variable = "s3:if-none-match"')==2
    assert 's3:DeleteObject' not in policy


@pytest.mark.parametrize('body',['{"job_id":"one","job_id":"two"}',' '*1025],ids=['duplicate-key','oversized'])
def test_poison_envelope_retries_without_claim_or_ack(monkeypatch,body):
    from threading import Event
    from app.processing_worker import execution
    from app.processing_worker.worker import SerialWorker,QueueMessage
    stop=Event();calls=[]
    class Transport:
        def receive_one(self,stop):return QueueMessage('synthetic-message',body,'synthetic-receipt')
        def retry(self,message):calls.append('retry');stop.set()
        def acknowledge(self,message):pytest.fail('poison message acknowledged')
        def close(self):calls.append('close')
    monkeypatch.setattr(execution,'execute_job',lambda *a,**kw:pytest.fail('invalid envelope reached claim'))
    SerialWorker(Transport(),stop=stop).run()
    assert calls==['retry','close']


@pytest.mark.parametrize('owned',[False,True])
@pytest.mark.parametrize('failure',[False,True])
def test_verifier_client_ownership_on_success_and_failure(monkeypatch,owned,failure):
    from app.lambda_worker import verifier_runtime as runtime
    configure_worker(monkeypatch)
    monkeypatch.setenv('DATARISE_VERIFIER_DB_SECRET_ARN','synthetic-reference')
    closed=[]
    client=SimpleNamespace(close=lambda:closed.append(True))
    monkeypatch.setattr(runtime.boto3,'client',lambda *a,**kw:client)
    class Service:
        def __init__(self,*a,**kw):pass
        def verify(self,candidate):
            if failure:raise RuntimeError('synthetic failure')
            return {'verified':True}
    monkeypatch.setattr(runtime,'RegistryVerifier',Service)
    verify=runtime.build_verifier(s3_client=None if owned else client,connection_factory=lambda:None)
    if failure:
        with pytest.raises(RuntimeError):verify(uuid4())
    else:assert verify(uuid4())=={'verified':True}
    assert closed==([True] if owned else [])


def test_verifier_budget_loss_after_work_does_not_confirm(monkeypatch):
    from app.lambda_worker import verifier_runtime as runtime
    configure_worker(monkeypatch)
    monkeypatch.setenv('DATARISE_VERIFIER_DB_SECRET_ARN','synthetic-reference')
    budgets=iter([10000,10000,4999])
    class Service:
        def __init__(self,*a,**kw):pass
        def verify(self,candidate):return {'verified':True}
    monkeypatch.setattr(runtime,'RegistryVerifier',Service)
    verify=runtime.build_verifier(s3_client=object(),connection_factory=lambda:None,remaining_time_ms=lambda:next(budgets))
    with pytest.raises(TimeoutError):verify(uuid4())


# Reuse the existing in-memory repository fixture while exercising the actual
# bootstrap, SQS transport, execution boundary, completion ordering and parser.
from tests.test_container_worker import job


@pytest.mark.parametrize('failure',[None,'executor','lease','completion','verifier'])
def test_shared_runtime_queue_to_completion_contract(job,monkeypatch,failure):
    from app.db import processing_job_repository as jobs
    from app.processing_worker import bootstrap
    configure_worker(monkeypatch)
    job_id,state=job
    worker=None
    deliveries=[str(job_id)]*(2 if failure is None else 1)
    class Queue:
        closed=False
        def receive_message(self,**kwargs):
            assert kwargs['MaxNumberOfMessages']==1
            if not deliveries:worker.stop.set();return {}
            return {'Messages':[{'MessageId':'one','Body':json.dumps({'job_id':deliveries.pop(0)}),'ReceiptHandle':'synthetic'}]}
        def change_message_visibility(self,**kwargs):state['events'].append('retry' if kwargs['VisibilityTimeout']==30 else 'visibility')
        def delete_message(self,**kwargs):
            assert state['status']=='SUCCEEDED'
            state['events'].append('ack')
        def close(self):self.closed=True
    queue=Queue()
    def executor(boundary):
        state['events'].append('execute')
        if failure=='executor':raise RuntimeError('synthetic execution failure')
        if failure=='lease':state['lost']=True
        if failure=='verifier':
            def unavailable(**kw):raise TimeoutError('synthetic response loss')
            LambdaVerifierClient(SimpleNamespace(invoke=unavailable),'synthetic-verifier')(uuid4())
    monkeypatch.setattr(bootstrap,'IntegratedExecutor',lambda *a,**kw:executor)
    if failure=='completion':
        def unavailable(*a,**kw):raise RuntimeError('synthetic database completion failure')
        monkeypatch.setattr(jobs,'finish_job',unavailable)
    worker=bootstrap.build_worker(sqs_client=queue,queue_url='synthetic-queue',storage_factory=lambda c:None,verifier=lambda c:None)
    worker.run()
    assert queue.closed and state['events'].count('execute')==1
    if failure is None:
        assert state['events'].count('ack')==2
        assert state['events'].index('success-commit')<state['events'].index('ack')
    else:
        assert 'ack' not in state['events'] and 'success-commit' not in state['events']
        assert 'retry' in state['events']


def test_secret_client_closes_when_bootstrap_fetch_fails(monkeypatch):
    monkeypatch.delitem(sys.modules,'app.db.database',raising=False)
    monkeypatch.delitem(sys.modules,'app.config',raising=False)
    monkeypatch.setenv('DATARISE_WORKER_DB_SECRET_ARN','synthetic-reference')
    closed=[]
    def fail(**kw):raise RuntimeError('sensitive synthetic SDK response')
    client=SimpleNamespace(get_secret_value=fail,close=lambda:closed.append(True))
    with pytest.raises(RuntimeError) as error:
        entry.initialize_database(SimpleNamespace(client=lambda *a,**kw:client))
    assert closed==[True]
    assert 'sensitive' not in ''.join(traceback.format_exception(error.value))


def test_preloaded_configuration_refuses_credential_initialization(monkeypatch):
    monkeypatch.delitem(sys.modules,'app.db.database',raising=False)
    monkeypatch.setitem(sys.modules,'app.config',SimpleNamespace())
    session=SimpleNamespace(client=lambda *a,**kw:pytest.fail('preloaded config reached secrets'))
    with pytest.raises(RuntimeError,match='configuration initialized'):
        entry.initialize_database(session)



def test_cold_container_import_and_disabled_cli_are_offline():
    import os,subprocess
    code="""
import sys,boto3
sys.dont_write_bytecode=True
def forbidden(*args,**kwargs):raise AssertionError('SDK constructed before approval')
boto3.Session=forbidden
from app.processing_worker.container_entrypoint import cli
assert 'app.db.database' not in sys.modules
assert 'app.config' not in sys.modules
assert cli()==2
"""
    env={key:os.environ[key] for key in ('PATH','SYSTEMROOT','TEMP') if key in os.environ}
    env.update(PYTHON_DOTENV_DISABLED='1',PYTHONDONTWRITEBYTECODE='1',AWS_EC2_METADATA_DISABLED='true',
        DATARISE_PROCESSING_DEPLOYMENT_APPROVED='false',DATARISE_PROCESSING_READERS='false')
    result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
