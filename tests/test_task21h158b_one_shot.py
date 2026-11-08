"""Offline one-shot contract over the shared real execution orchestration."""
import json
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.db import processing_job_repository as jobs
from app.processing_worker.one_shot import run_once, main, Invocation, Outcome, ExitCode, Stage


@pytest.fixture
def runtime(monkeypatch):
    lease=jobs.JobLease(uuid4(),uuid4(),datetime.now(timezone.utc)+timedelta(seconds=120),1)
    state=SimpleNamespace(lease=lease,claims=0,finishes=[],executions=0,time=0.0,operation='BRONZE',available=True)
    def claim(job_id):
        assert job_id==lease.job_id;state.claims+=1
        return jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED,lease)
    def finish(job_id,token,**kw):
        assert (job_id,token)==(lease.job_id,lease.lease_token)
        state.finishes.append(kw)
        return {'status':'RETRY_WAIT' if kw else 'SUCCEEDED'}
    def scope(*a,**kw):
        return jobs.JobExecutionScope(1,2,3,4,5,state.operation) if state.available else None
    monkeypatch.setattr(jobs,'claim_job_result',claim)
    monkeypatch.setattr(jobs,'finish_job',finish)
    monkeypatch.setattr(jobs,'get_execution_scope',scope)
    monkeypatch.setattr(jobs,'renew_job_lease',lambda *a,**kw:lease if state.available else None)
    def execute(boundary):state.executions+=1
    state.execute=execute
    state.run=lambda **kw:run_once(str(lease.job_id),executor=kw.pop('executor',execute),runtime_approval=kw.pop('runtime_approval',lambda:True),clock=lambda:state.time,**kw)
    return state


@pytest.mark.parametrize('outcome',list(jobs.ClaimOutcome))
def test_all_seven_claim_outcomes_are_distinct(runtime,monkeypatch,outcome):
    retry_at=datetime.now(timezone.utc)+timedelta(seconds=20) if outcome in (jobs.ClaimOutcome.BUSY,jobs.ClaimOutcome.NOT_DUE) else None
    monkeypatch.setattr(jobs,'claim_job_result',lambda _:jobs.ClaimResult(outcome,runtime.lease if outcome==jobs.ClaimOutcome.CLAIMED else None,retry_at))
    result=runtime.run()
    assert result.outcome==(Outcome.SUCCEEDED if outcome==jobs.ClaimOutcome.CLAIMED else Outcome(outcome.value))
    assert runtime.executions==(1 if outcome==jobs.ClaimOutcome.CLAIMED else 0)
    assert len(runtime.finishes)==(1 if outcome==jobs.ClaimOutcome.CLAIMED else 0)
    assert result.retry_at==retry_at
    assert result.completion_observed==(outcome in (jobs.ClaimOutcome.CLAIMED,jobs.ClaimOutcome.ALREADY_SUCCEEDED))
    if outcome in (jobs.ClaimOutcome.BUSY,jobs.ClaimOutcome.NOT_DUE):assert result.exit_code==75
    if outcome==jobs.ClaimOutcome.TERMINAL_FAILED:assert result.exit_code==20
    if outcome==jobs.ClaimOutcome.SOURCE_INELIGIBLE:assert result.exit_code==21
    if outcome==jobs.ClaimOutcome.NOT_FOUND:assert result.exit_code==66


@pytest.mark.parametrize('stage',list(Stage))
def test_stage_is_resolved_from_authorized_scope(runtime,stage):
    runtime.operation=stage.value
    result=runtime.run()
    assert result.outcome==Outcome.SUCCEEDED and result.stage==stage
    assert result.exit_code==0 and runtime.claims==runtime.executions==1


@pytest.mark.parametrize('value',[None,{},[],True,123,'', 's3://private/source', 'SELECT 1', str(uuid4())+'?stage=GOLD','00000000-0000-0000-0000-000000000000', uuid4().hex])
def test_malformed_invocation_cannot_reach_repo(runtime,value):
    result=run_once(value,executor=runtime.execute,runtime_approval=lambda:True)
    assert result.outcome==Outcome.INVALID_INVOCATION and result.exit_code==64
    assert runtime.claims==runtime.executions==0
    assert result.job_id is None and result.submission_id is None


def test_submission_identity_is_opaque_uuid_not_execution_scope(runtime):
    correlation=uuid4();result=runtime.run(submission_id=correlation)
    assert result.submission_id==correlation and result.job_id==runtime.lease.job_id
    assert result.as_dict()['submission_id']==str(correlation)
    assert Invocation(str(runtime.lease.job_id).upper()).job_id==runtime.lease.job_id
    bad=runtime.run(submission_id='private://secret')
    assert bad.outcome==Outcome.INVALID_INVOCATION and bad.submission_id is None


@pytest.mark.parametrize('options',[{}, {'executor':lambda _:None},{'runtime_approval':lambda:True},{'executor':lambda _:None,'runtime_approval':lambda:False}])
def test_missing_or_denied_injection_is_disabled_before_claim(runtime,options):
    result=run_once(runtime.lease.job_id,**options)
    assert result.outcome==Outcome.DISABLED and result.exit_code==78
    assert runtime.claims==0


@pytest.mark.parametrize('budget',[True,0,5,901,1.5,'90'])
def test_invocation_budget_is_bounded(runtime,budget):
    assert runtime.run(budget_seconds=budget).outcome==Outcome.DISABLED
    assert runtime.claims==0


def test_cancel_before_claim_does_not_touch_repo(runtime):
    stop=Event();stop.set()
    result=runtime.run(stop=stop)
    assert result.outcome==Outcome.CANCELLED and result.exit_code==75
    assert runtime.claims==0


def test_preparation_consumes_deadline_without_claim(runtime):
    def approval():runtime.time=89;return True
    result=runtime.run(runtime_approval=approval)
    assert result.outcome==Outcome.DEADLINE_EXCEEDED and runtime.claims==0


@pytest.mark.parametrize('cause',['cancel','deadline'])
def test_stop_after_blocking_callback_cannot_mark_success(runtime,cause):
    stop=Event()
    def blocking_returns(boundary):
        runtime.executions+=1
        if cause=='cancel':stop.set()
        else:runtime.time=100
    result=runtime.run(executor=blocking_returns,stop=stop)
    assert result.outcome==(Outcome.CANCELLED if cause=='cancel' else Outcome.DEADLINE_EXCEEDED)
    assert result.completion_observed is False and result.exit_code==75
    assert runtime.finishes==[dict(failure_code='PROCESSING_FAILED',retry=True,retry_delay_seconds=30)]


@pytest.mark.parametrize('when',['before','during'])
def test_expired_or_stolen_lease_prevents_completion(runtime,when):
    if when=='before':runtime.available=False
    def lost(boundary):runtime.available=False
    result=runtime.run(executor=lost)
    assert result.outcome==Outcome.OWNERSHIP_UNAVAILABLE
    assert result.completion_observed is False
    assert runtime.finishes==[dict(failure_code='PROCESSING_FAILED',retry=True,retry_delay_seconds=30)]


def test_unknown_persisted_stage_cannot_execute(runtime):
    runtime.operation='CONTINUE'
    result=runtime.run()
    assert result.outcome==Outcome.UNSUPPORTED_STAGE and result.exit_code==65
    assert runtime.executions==0


def test_executor_failure_and_false_success_payload_never_succeed(runtime):
    def bad(_):raise ValueError('SYNTHETIC_PRIVATE s3://private secret')
    result=runtime.run(executor=bad)
    assert result.outcome==Outcome.RETRY_REQUIRED and 'SYNTHETIC_PRIVATE' not in json.dumps(result.as_dict())
    assert runtime.run(executor=lambda _: {'status':'SUCCESS'}).outcome==Outcome.RETRY_REQUIRED


def test_lost_completion_response_reconciles_duplicate(runtime,monkeypatch):
    committed=[]
    def finish(*a,**kw):committed.append(True);raise ConnectionError('private DB')
    monkeypatch.setattr(jobs,'finish_job',finish)
    first=runtime.run()
    assert first.outcome==Outcome.RETRY_REQUIRED and first.exit_code==75
    monkeypatch.setattr(jobs,'claim_job_result',lambda _:jobs.ClaimResult(jobs.ClaimOutcome.ALREADY_SUCCEEDED))
    second=runtime.run()
    assert second.outcome==Outcome.ALREADY_SUCCEEDED and second.exit_code==0
    assert runtime.executions==1 and len(committed)==1


def test_temporary_database_failure_is_safe_and_not_success(runtime,monkeypatch):
    monkeypatch.setattr(jobs,'claim_job_result',lambda _:(_ for _ in ()).throw(ConnectionError('SYNTHETIC_PRIVATE')))
    result=runtime.run()
    assert result.outcome==Outcome.RETRY_REQUIRED and result.exit_code==75
    assert runtime.executions==0 and 'SYNTHETIC_PRIVATE' not in repr(result)


def test_database_failure_after_claim_records_only_safe_failure(runtime,monkeypatch):
    monkeypatch.setattr(jobs,'get_execution_scope',lambda *a,**kw:(_ for _ in ()).throw(ConnectionError('private database location')))
    result=runtime.run()
    assert result.outcome==Outcome.RETRY_REQUIRED and runtime.executions==0
    assert runtime.finishes==[dict(failure_code='PROCESSING_FAILED',retry=True,retry_delay_seconds=30)]


def test_client_stage_override_is_not_an_invocation_parameter(runtime):
    with pytest.raises(TypeError):run_once(runtime.lease.job_id,stage='GOLD')
    assert runtime.claims==0


def test_fresh_process_default_does_not_import_database_or_sdk():
    import subprocess,sys,os
    code="""
import sys
from app.processing_worker.one_shot import main
assert main(['11111111-1111-4111-8111-111111111111'])==78
assert 'app.db.database' not in sys.modules
assert 'boto3' not in sys.modules
assert 'app.config' not in sys.modules
"""
    completed=subprocess.run([sys.executable,'-c',code],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=15)
    assert completed.returncode==0,completed.stderr
    assert json.loads(completed.stdout)['outcome']=='DISABLED'


def test_result_does_not_serialize_lease_or_source_scope(runtime):
    result=runtime.run();body=json.dumps(result.as_dict())
    assert str(runtime.lease.lease_token) not in body+repr(result)
    assert set(result.as_dict())=={'version','outcome','exit_code','job_id','submission_id','stage','retry_at','completion_observed'}
    assert not any(key in body for key in ('lease_token','upload_id','workspace_id','dataset_id','requested_by','s3://'))


def test_process_crash_is_not_swallowed(runtime):
    class Crash(BaseException):pass
    def crash(_):raise Crash()
    with pytest.raises(Crash):runtime.run(executor=crash)
    assert runtime.finishes==[]


def test_cli_is_disabled_and_malformed_arguments_are_sanitized(runtime,capsys):
    assert main([str(runtime.lease.job_id)])==78
    assert json.loads(capsys.readouterr().out)['outcome']=='DISABLED'
    assert main([str(runtime.lease.job_id),'--stage','GOLD','SYNTHETIC_PRIVATE'])==64
    assert 'SYNTHETIC_PRIVATE' not in capsys.readouterr().out
    assert runtime.claims==0
