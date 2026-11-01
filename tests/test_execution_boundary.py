from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db import processing_job_repository as jobs
from app.lambda_worker.execution_boundary import ExecutionBoundary, ExecutionStopped
from app.lambda_worker import service


@pytest.fixture
def boundary(monkeypatch):
    lease = jobs.JobLease(uuid4(), uuid4(), datetime.now(timezone.utc)+timedelta(seconds=120), 1)
    scope = jobs.JobExecutionScope(1, 2, 3, 4, 5, 'CONTINUE')
    monkeypatch.setattr(jobs, 'get_execution_scope', lambda *a, **kw: scope)
    monkeypatch.setattr(jobs, 'renew_job_lease', lambda *a, **kw: lease)
    return ExecutionBoundary(lease, lambda: 300000)


@pytest.mark.parametrize('remaining', [0, 5000, 6000, None, -1])
def test_insufficient_stage_time_never_admits_work(boundary, monkeypatch, remaining):
    boundary.remaining_time_ms = lambda: remaining
    monkeypatch.setattr(jobs, 'get_execution_scope', lambda *a, **kw: pytest.fail('No DB call with insufficient time'))
    with pytest.raises(ExecutionStopped):
        boundary.checkpoint(required_ms=1000, renew=True)


def test_db_preparation_consumes_time_and_stops_boundary(boundary):
    times = iter([100000, 5000])
    boundary.remaining_time_ms = lambda: next(times)
    with pytest.raises(ExecutionStopped, match='time'):
        boundary.checkpoint(required_ms=1000, renew=True)
    boundary.remaining_time_ms = lambda: 300000
    with pytest.raises(ExecutionStopped, match='stopped'):
        boundary.checkpoint()


@pytest.mark.parametrize('loss', ['before_renewal', 'renewal', 'after_renewal'])
def test_lease_loss_before_stage_prevents_entry(boundary, monkeypatch, loss):
    if loss == 'renewal':
        monkeypatch.setattr(jobs, 'renew_job_lease', lambda *a, **kw: None)
    else:
        original = jobs.get_execution_scope
        answers = iter([None] if loss == 'before_renewal' else [original(), None])
        monkeypatch.setattr(jobs, 'get_execution_scope', lambda *a, **kw: next(answers))
    with pytest.raises(ExecutionStopped):
        with boundary.stage(required_ms=1000):
            pytest.fail('A lost lease must not admit a stage')


def test_renewal_updates_lease_and_checks_stage_budget(boundary, monkeypatch):
    renewed = jobs.JobLease(boundary.lease.job_id, boundary.lease.lease_token,
                           boundary.lease.lease_expires_at+timedelta(seconds=60), 1)
    calls = []
    def renew(job_id, token, **kwargs):
        calls.append((job_id, token, kwargs))
        return renewed
    monkeypatch.setattr(jobs, 'renew_job_lease', renew)
    boundary.checkpoint(required_ms=1000, renew=True)
    assert boundary.lease == renewed
    assert calls == [(renewed.job_id, renewed.lease_token, {'lease_seconds': 120})]


def test_existing_locks_release_on_stage_exception(boundary, monkeypatch):
    from app.db import dataset_repository
    from app.services import dataset_processing_service, delivery_lifecycle_service, delivery_execution_service
    events = []
    def lock(name):
        @contextmanager
        def held(*args):
            events.append('enter '+name)
            try:
                yield
            finally:
                events.append('exit '+name)
        return held
    monkeypatch.setattr(dataset_processing_service, 'dataset_lock', lock('dataset'))
    monkeypatch.setattr(delivery_lifecycle_service, 'lifecycle_lock', lock('lifecycle'))
    monkeypatch.setattr(delivery_execution_service, 'delivery_lock', lock('delivery'))
    monkeypatch.setattr(dataset_repository, 'get_upload_processing_association', lambda *a: (6,))
    with pytest.raises(RuntimeError, match='stage failure'):
        with boundary.stage(required_ms=1000):
            raise RuntimeError('stage failure')
    assert events == ['enter dataset', 'enter lifecycle', 'enter delivery',
                      'exit delivery', 'exit lifecycle', 'exit dataset']
    with pytest.raises(ExecutionStopped):
        boundary.checkpoint()


def test_opt_in_executor_receives_boundary_and_can_complete(boundary, monkeypatch):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED, boundary.lease))
    monkeypatch.setattr(jobs, 'finish_job', lambda *a, **kw: {'status': 'SUCCEEDED'})
    called = []
    def execute(control):
        assert isinstance(control, ExecutionBoundary)
        control.checkpoint(renew=True)
        called.append(control.lease.job_id)
    service.execute_job(str(boundary.lease.job_id), boundary_executor=execute, remaining_time_ms=lambda: 300000)
    assert called == [boundary.lease.job_id]


@pytest.mark.parametrize('kwargs', [{'boundary_executor': lambda _: None},
    {'boundary_executor': lambda _: None, 'executor': lambda _: None, 'remaining_time_ms': lambda: 300000}])
def test_boundary_requires_explicit_clock_and_exclusive_executor(kwargs):
    with pytest.raises(ValueError):
        service.execute_job(str(uuid4()), **kwargs)


@pytest.mark.parametrize('seconds', [0, 901, True])
def test_renewal_bounds_are_checked_before_database(monkeypatch, seconds):
    monkeypatch.setattr(jobs, 'get_connection', lambda: pytest.fail('No database for invalid interval'))
    with pytest.raises(ValueError):
        jobs.renew_job_lease(uuid4(), uuid4(), lease_seconds=seconds)


def test_controlled_executor_cannot_ack_after_swallowed_stop(boundary, monkeypatch):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED, boundary.lease))
    completions = []
    def finish(*a, **kw):
        completions.append(kw)
        return {'status': 'RETRY_WAIT'}
    monkeypatch.setattr(jobs, 'finish_job', finish)
    def execute(control):
        control.remaining_time_ms = lambda: 0
        with pytest.raises(ExecutionStopped):
            control.checkpoint()
    with pytest.raises(ExecutionStopped):
        service.execute_job(str(boundary.lease.job_id), boundary_executor=execute, remaining_time_ms=lambda: 300000)
    assert completions == [{'failure_code': 'PROCESSING_FAILED', 'retry': True, 'retry_delay_seconds': 30}]


def test_renewal_interval_is_capped_by_invocation_budget(boundary, monkeypatch):
    boundary.remaining_time_ms = lambda: 20000
    intervals = []
    def renew(*args, **kwargs):
        intervals.append(kwargs['lease_seconds'])
        return boundary.lease
    monkeypatch.setattr(jobs, 'renew_job_lease', renew)
    boundary.checkpoint(required_ms=1000, renew=True)
    assert intervals == [15]


def test_controlled_executor_is_not_called_without_invocation_time(boundary, monkeypatch):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED, boundary.lease))
    monkeypatch.setattr(jobs, 'finish_job', lambda *a, **kw: {'status': 'RETRY_WAIT'})
    with pytest.raises(ExecutionStopped):
        service.execute_job(str(boundary.lease.job_id), remaining_time_ms=lambda: 1000,
            boundary_executor=lambda _: pytest.fail('No execution after the time boundary'))
