"""Real spawn processes on the local OS; no SDK/database use in these cases."""
import json
import multiprocessing as mp
import os
from pathlib import Path
from threading import Event,Thread
import time
from uuid import uuid4
import pytest

from app.processing_worker.supervision import supervise,Settings,with_signals
from app.processing_worker.one_shot import Outcome
from task21h158d_support import fake_agent,fake_child


def settings(**overrides):
    return Settings(**dict(dict(budget_seconds=8,lease_seconds=8,heartbeat_seconds=.25,database_response_seconds=2,grace_seconds=.2,kill_seconds=.5),**overrides))


def run(root,**kwargs):
    return supervise(uuid4(),agent_factory=fake_agent,child_factory=fake_child,configuration={'root':str(root),**kwargs.pop('configuration',{})},approval=lambda:True,settings=kwargs.pop('settings',settings()),scratch_directory=root,**kwargs)


def until(predicate,seconds=10):
    end=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>end:raise AssertionError('Bounded process readiness not observed')
        Event().wait(.02)


def test_default_and_malformed_start_nothing():
    assert supervise(uuid4()).result.outcome==Outcome.DISABLED
    assert supervise('invalid').result.outcome==Outcome.INVALID_INVOCATION
    assert not mp.active_children()


@pytest.mark.parametrize('field,value',[('lease_seconds',121),('heartbeat_seconds',60),('budget_seconds',901),('poll_seconds',1),('database_response_seconds',120),('grace_seconds',8),('kill_seconds',float('nan'))])
def test_unsafe_settings_rejected(field,value):
    with pytest.raises(ValueError):Settings(**{field:value})


@pytest.mark.parametrize('outcome',['ALREADY_SUCCEEDED','TERMINAL_FAILED','SOURCE_INELIGIBLE','BUSY','NOT_DUE','NOT_FOUND'])
def test_claim_outcomes_never_launch_execution(tmp_path,outcome):
    result=run(tmp_path,configuration={'outcome':outcome})
    assert result.result.outcome.value==outcome and result.children_reaped
    assert not (tmp_path/'ready').exists()


def test_injected_completion_is_confirmed_in_repository(tmp_path):
    result=run(tmp_path)
    assert result.result.outcome==Outcome.SUCCEEDED and result.exit_code==0 and result.children_reaped
    assert (tmp_path/'status').read_text()=='SUCCEEDED'
    assert not mp.active_children()
    assert 'lease_token' not in json.dumps(result.as_dict())


@pytest.mark.parametrize('fault',['executor_fail','completion_fail','crash','heartbeat_fail','heartbeat_hang'])
def test_failure_never_reports_success_and_children_reaped(tmp_path,fault):
    started=time.monotonic()
    result=run(tmp_path,configuration={'fault':fault})
    assert result.exit_code!=0 and not result.result.completion_observed and result.children_reaped
    assert not mp.active_children()
    path=tmp_path/'renewals';before=path.read_bytes() if path.exists() else b''
    Event().wait(.1)
    assert (path.read_bytes() if path.exists() else b'')==before
    assert time.monotonic()-started<20


def test_blocking_call_gets_independent_renewals_and_deadline_stop(tmp_path):
    start=time.monotonic();result=run(tmp_path,configuration={'fault':'hang'})
    assert (tmp_path/'ready').exists() and result.renewals>=2
    assert result.result.outcome==Outcome.DEADLINE_EXCEEDED and result.children_reaped
    assert time.monotonic()-start<15


def test_cooperative_parent_stop_reaps_uncooperative_child(tmp_path):
    stop=Event()
    def cancel():until(lambda:(tmp_path/'ready').exists());stop.set()
    worker=Thread(target=cancel);worker.start()
    try:result=run(tmp_path,stop=stop,configuration={'fault':'hang'})
    finally:worker.join(12)
    assert result.result.outcome==Outcome.CANCELLED and result.children_reaped
    assert not mp.active_children()


def test_observed_memory_limit_is_not_an_os_allocation_limit(tmp_path):
    result=run(tmp_path,memory_bytes=1)
    assert result.exit_code!=0 and result.children_reaped


def test_signal_wrapper_restores_handlers_and_preserves_return():
    import signal
    stop=Event();before=signal.getsignal(signal.SIGTERM)
    def body():signal.raise_signal(signal.SIGTERM);return 75
    assert with_signals(body,stop)==75 and stop.is_set()
    assert signal.getsignal(signal.SIGTERM)==before


def test_parent_death_contains_owned_child_on_actual_platform(tmp_path):
    import subprocess,sys
    env=dict(os.environ,PYTHONPATH=os.pathsep.join((str(Path('tests').resolve()),str(Path.cwd()))))
    parent=subprocess.Popen([sys.executable,'-B','-c','from task21h158d_support import host; import sys; host(sys.argv[1])',str(tmp_path)],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env)
    handle=None
    try:
        until(lambda:(tmp_path/'ready').exists())
        pid=int((tmp_path/'ready').read_text())
        if os.name=='nt':
            import ctypes as c
            from ctypes import wintypes as w
            kernel=c.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
            kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.WaitForSingleObject.restype=w.DWORD
            kernel.CloseHandle.argtypes=[w.HANDLE]
            handle=kernel.OpenProcess(0x100000,False,pid)
            assert handle
        parent.kill();parent.wait(timeout=5)
        if os.name=='nt':assert kernel.WaitForSingleObject(handle,5000)==0
        else:
            until(lambda:not Path(f'/proc/{pid}').exists() or '\nState:\tZ' in Path(f'/proc/{pid}/status').read_text())
        assert not (tmp_path/'result.json').exists()
    finally:
        if parent.poll() is None:parent.kill();parent.wait(timeout=5)
        if handle:kernel.CloseHandle(handle)


def test_completion_clears_lease_before_child_exit_without_false_failure(tmp_path):
    result=run(tmp_path,configuration={'fault':'completion_race'})
    assert (tmp_path/'terminal-observed').exists()
    assert result.result.outcome==Outcome.SUCCEEDED and result.children_reaped


def test_unpicklable_composition_fails_without_leaking_processes(tmp_path):
    result=supervise(uuid4(),agent_factory=lambda _:None,child_factory=fake_child,approval=lambda:True,
                     scratch_directory=tmp_path,settings=settings())
    assert result.exit_code!=0 and result.children_reaped and not mp.active_children()


def test_supervisor_module_is_disabled_without_trusted_composition():
    import subprocess,sys
    result=subprocess.run([sys.executable,'-B','-m','app.processing_worker.supervision'],capture_output=True,timeout=10)
    assert result.returncode==78 and not result.stdout and not result.stderr


def test_unconfirmed_cleanup_cannot_return_success(tmp_path,monkeypatch):
    from app.processing_worker import supervision
    original=supervision.reap
    def unconfirmed(*args):original(*args);return False
    monkeypatch.setattr(supervision,'reap',unconfirmed)
    result=run(tmp_path)
    assert result.result.outcome==Outcome.RETRY_REQUIRED and not result.children_reaped
    assert (tmp_path/'status').read_text()=='SUCCEEDED'
    assert not mp.active_children()
