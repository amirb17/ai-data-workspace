"""Dispatcher lifecycle and uncertain-send recovery using injected local fakes."""
import json
import traceback
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.processing_worker import dispatcher
from app.processing_worker.dispatch import dispatch_one
from app.db import database
from app.processing_worker.runtime_db_secret import database_url_from_secret
from app.processing_worker.runtime_security import require_runtime


@pytest.fixture
def approved(monkeypatch):
    for flag in ('DATARISE_PROCESSING_DEPLOYMENT_APPROVED','DATARISE_PROCESSING_READERS','DATARISE_DISPATCH_APPROVED'):
        monkeypatch.setenv(flag,'true')
    monkeypatch.setenv('AWS_REGION','ap-southeast-2')
    monkeypatch.setenv('AWS_DEFAULT_REGION','ap-southeast-2')


class Stop:
    def __init__(self):self.stopped=False;self.waits=[]
    def is_set(self):return self.stopped
    def set(self):self.stopped=True
    def wait(self,seconds):self.waits.append(seconds);return self.stopped


def test_disabled_supervisor_does_not_admit_work():
    with pytest.raises(PermissionError):dispatcher.DispatchSupervisor(lambda:True,close=lambda:None)


@pytest.mark.parametrize('flag',['DATARISE_PROCESSING_DEPLOYMENT_APPROVED','DATARISE_PROCESSING_READERS','DATARISE_DISPATCH_APPROVED'])
def test_each_flag_is_required_before_dispatch(approved,monkeypatch,flag):
    monkeypatch.setenv(flag,'false');closed=[]
    loop=dispatcher.DispatchSupervisor(lambda:pytest.fail('disabled dispatch'),close=lambda:closed.append(True),enabled=True)
    with pytest.raises(PermissionError):loop.run()
    assert closed==[True]


def test_backoff_resets_after_success_and_waits_are_interruptible(approved):
    stop=Stop();closed=[];script=iter([RuntimeError(),RuntimeError(),True,RuntimeError(),False])
    def once():
        value=next(script)
        if value is False:stop.set()
        if isinstance(value,Exception):raise value
        return value
    dispatcher.DispatchSupervisor(once,close=lambda:closed.append(True),stop=stop,enabled=True).run()
    assert stop.waits==[1,2,1,1,3] and closed==[True]


def test_error_budget_closes_and_sanitizes(approved,caplog):
    stop=Stop();closed=[]
    def once():raise RuntimeError('SYNTHETIC_SENSITIVE_RESPONSE')
    loop=dispatcher.DispatchSupervisor(once,close=lambda:closed.append(True),stop=stop,retry_cap_seconds=2,error_limit=4,enabled=True)
    with pytest.raises(RuntimeError) as error:loop.run()
    assert stop.waits==[1,2,2] and closed==[True]
    assert 'SYNTHETIC' not in caplog.text+''.join(traceback.format_exception(error.value))


def test_shutdown_before_claim_and_during_send(approved):
    stop=Stop();stop.set();events=[]
    dispatcher.DispatchSupervisor(lambda:pytest.fail('claimed after stop'),close=lambda:events.append('close'),stop=stop,enabled=True).run()
    assert events==['close']
    stop=Stop();events=[]
    def once():
        events.append('send');stop.set();events.append('durable-mark');return True
    dispatcher.DispatchSupervisor(once,close=lambda:events.append('close'),stop=stop,enabled=True).run()
    assert events==['send','durable-mark','close']


@pytest.mark.parametrize('value',[0,True,61,-1])
def test_invalid_supervision_bounds(value):
    with pytest.raises(ValueError):dispatcher.DispatchSupervisor(lambda:False,close=lambda:None,error_limit=value,enabled=True)


def test_dispatch_has_no_ambient_worker_aws_identity(approved):
    with pytest.raises(PermissionError,match='identity'):dispatcher.main()


def test_dispatch_rejects_shared_secret_reference(approved,monkeypatch):
    monkeypatch.setenv('AWS_DEFAULT_REGION','ap-southeast-2')
    monkeypatch.setenv('DATARISE_PROCESSING_QUEUE_URL','synthetic')
    monkeypatch.setenv('DATARISE_DISPATCH_DB_SECRET_ARN','same-reference')
    monkeypatch.setenv('DATARISE_WORKER_DB_SECRET_ARN','same-reference')
    with pytest.raises(PermissionError,match='distinct'):dispatcher.main(session=object())


def test_dispatch_database_role_does_not_replace_worker_identity():
    doc=json.dumps(dict(username='datarise_dispatch',password='synthetic-only',host='db.example.test',port=5432,dbname='test'))
    assert database_url_from_secret(doc,'datarise_dispatch').startswith('postgresql://datarise_dispatch:')
    with pytest.raises(ValueError):database_url_from_secret(doc,'datarise_worker')


class Permissions:
    def __init__(self,functions,job_write=False):self.functions=functions;self.job_write=job_write;self.value=None
    def execute(self,sql,args=()):
        if 'current_schema()' in sql and 'inet_server_addr' in sql:self.value=('synthetic','10.0.0.1',False,False)
        elif 'has_function_privilege' in sql:
            function=args[0] if args else next(x for x in ['task20_verify(uuid,uuid,uuid,jsonb)','task20_publish(uuid,uuid,uuid)','task20_claim_dispatch()','task20_dispatched(uuid,uuid)'] if x in sql)
            self.value=function in self.functions
        elif "'processing_jobs'" in sql:self.value=self.job_write
        else:self.value=False
        return self
    def fetchone(self):return (self.value,) if not isinstance(self.value,tuple) else self.value


def test_complete_dispatch_capabilities_without_job_or_publication_authority(approved):
    require_runtime(Permissions({'task20_claim_dispatch()','task20_dispatched(uuid,uuid)'}),'dispatch')


@pytest.mark.parametrize('fault',['missing-mark','job-write','publish','verify','worker-dispatch'])
def test_runtime_rejects_incomplete_or_combined_authorities(approved,fault):
    functions={'task20_claim_dispatch()','task20_dispatched(uuid,uuid)'};purpose='dispatch'
    if fault=='missing-mark':functions.remove('task20_dispatched(uuid,uuid)')
    if fault=='publish':functions.add('task20_publish(uuid,uuid,uuid)')
    if fault=='verify':functions.add('task20_verify(uuid,uuid,uuid,jsonb)')
    if fault=='worker-dispatch':purpose='worker';functions.add('task20_publish(uuid,uuid,uuid)')
    with pytest.raises(PermissionError):require_runtime(Permissions(functions,job_write=fault=='job-write'),purpose)


class Outbox:
    def __init__(self):self.now=0;self.until=0;self.token=None;self.delivered=False;self.job=uuid4();self.events=[];self.mark_fail=False
    def connect(self):
        state=self
        class Conn:
            def __init__(self):self.pending=None
            def __enter__(self):return self
            def __exit__(self,kind,*rest):
                if kind is None and self.pending:self.pending()
                state.events.append('commit' if kind is None else 'rollback')
            def execute(self,sql,args=()):
                assert database._transaction_connection.get() is None
                if 'task20_claim_dispatch' in sql:
                    if state.delivered or state.until>state.now:self.value=None
                    else:
                        token=uuid4();self.value=(state.job,token)
                        def commit():state.token=token;state.until=state.now+60
                        self.pending=commit
                else:
                    assert 'task20_dispatched' in sql
                    if state.mark_fail:raise RuntimeError('synthetic mark failure')
                    ok=args==(state.job,state.token) and state.now<state.until
                    self.value=(ok,)
                    if ok:self.pending=lambda:setattr(state,'delivered',True)
                return self
            def fetchone(self):return self.value
        return Conn()


@pytest.mark.parametrize('fault',['lost-send-response','mark-failure','stale-mark'])
def test_real_dispatch_operation_recovers_uncertain_delivery(monkeypatch,fault):
    from app.processing_worker import runtime_security
    state=Outbox();monkeypatch.setattr(database,'get_connection',state.connect)
    monkeypatch.setattr(runtime_security,'require_runtime',lambda conn,purpose,**kwargs:None)
    calls=[];failed=True
    class Sender:
        def send_message(self,**kwargs):
            nonlocal failed
            assert state.events[-1]=='commit' and state.token is not None
            assert json.loads(kwargs['MessageBody'])=={'job_id':str(state.job)}
            calls.append(kwargs['MessageBody'])
            if failed:
                failed=False
                if fault=='lost-send-response':raise TimeoutError('synthetic accepted send, lost response')
                if fault=='mark-failure':state.mark_fail=True
                if fault=='stale-mark':state.token=uuid4()
    outer=database._transaction_connection.set(object())
    try:
        if fault=='stale-mark':assert dispatch_one(Sender(),'synthetic',enabled=True) is False
        else:
            with pytest.raises((TimeoutError,RuntimeError)):dispatch_one(Sender(),'synthetic',enabled=True)
        assert not state.delivered
        assert dispatch_one(Sender(),'synthetic',enabled=True) is False
        state.now+=61;state.mark_fail=False
        assert dispatch_one(Sender(),'synthetic',enabled=True) is True
        assert state.delivered and len(calls)==2
    finally:database._transaction_connection.reset(outer)



def test_empty_poll_backoff_is_bounded_and_resets(approved):
    stop=Stop();script=iter([False,False,True,False]);calls=0
    def once():
        nonlocal calls
        calls+=1
        if calls==4:stop.set()
        return next(script)
    dispatcher.DispatchSupervisor(once,close=lambda:None,stop=stop,enabled=True).run()
    assert stop.waits==[3,6,1,3]


@pytest.mark.parametrize('fail',[False,True])
def test_dispatch_runtime_preflight_precedes_sender_and_cleans_up(approved,monkeypatch,fail):
    from app.processing_worker import container_entrypoint,process_lifecycle
    monkeypatch.setenv('AWS_DEFAULT_REGION','ap-southeast-2')
    monkeypatch.setenv('DATARISE_PROCESSING_QUEUE_URL','synthetic')
    monkeypatch.setenv('DATARISE_DISPATCH_DB_SECRET_ARN','separate-synthetic-reference')
    monkeypatch.delenv('DATARISE_WORKER_DB_SECRET_ARN',raising=False)
    monkeypatch.delenv('DATARISE_VERIFIER_DB_SECRET_ARN',raising=False)
    events=[];stop=Stop();stop.set()
    def initialize(session,*,purpose):
        events.append(purpose)
        if fail:raise PermissionError('synthetic permission refusal')
    monkeypatch.setattr(container_entrypoint,'initialize_database',initialize)
    sender=SimpleNamespace(close=lambda:events.append('close'))
    def client(service,**kwargs):events.append(service);return sender
    session=SimpleNamespace(client=client,region_name="ap-southeast-2")
    monkeypatch.setattr(process_lifecycle,'run_with_signals',lambda run,stop,close:(run() or 0))
    if fail:
        with pytest.raises(PermissionError):dispatcher.main(session=session,stop=stop)
        assert events==['dispatch']
    else:
        assert dispatcher.main(session=session,stop=stop)==0
        assert events==['dispatch','sqs','close']



def test_dispatch_session_region_must_match_reviewed_environment(approved,monkeypatch):
    monkeypatch.setenv('DATARISE_PROCESSING_QUEUE_URL','synthetic')
    monkeypatch.setenv('DATARISE_DISPATCH_DB_SECRET_ARN','dedicated-synthetic-reference')
    session=SimpleNamespace(region_name='different',client=lambda *a,**kw:pytest.fail('wrong region reached SDK'))
    with pytest.raises(PermissionError,match='region binding'):dispatcher.main(session=session)
