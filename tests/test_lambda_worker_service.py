from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db import processing_job_repository as jobs
from app.lambda_worker import service


@pytest.fixture
def lease():
    return jobs.JobLease(uuid4(), uuid4(), datetime.now(timezone.utc)+timedelta(minutes=2), 1)


def claimed(monkeypatch, lease):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda job_id: jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED, lease))


@pytest.mark.parametrize('outcome', [jobs.ClaimOutcome.ALREADY_SUCCEEDED,
                                   jobs.ClaimOutcome.TERMINAL_FAILED, jobs.ClaimOutcome.SOURCE_INELIGIBLE])
def test_terminal_outcomes_ack_without_execution(monkeypatch, lease, outcome):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: jobs.ClaimResult(outcome))
    monkeypatch.setattr(jobs, 'finish_job', lambda *a, **kw: pytest.fail('Terminal jobs must not be completed again'))
    assert service.execute_job(str(lease.job_id), executor=lambda _: pytest.fail('Must not execute')) is None


@pytest.mark.parametrize('outcome', [jobs.ClaimOutcome.BUSY, jobs.ClaimOutcome.NOT_DUE, jobs.ClaimOutcome.NOT_FOUND])
def test_retry_outcomes_never_execute(monkeypatch, lease, outcome):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: jobs.ClaimResult(outcome))
    monkeypatch.setattr(jobs, 'finish_job', lambda *a, **kw: pytest.fail('Must not finish without claim'))
    with pytest.raises(service.JobRetryRequired, match=outcome.value):
        service.execute_job(str(lease.job_id), executor=lambda _: pytest.fail('Must not execute'))


def test_success_and_duplicate_require_only_one_execution(monkeypatch, lease):
    events = []
    outcomes = iter([jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED, lease),
                     jobs.ClaimResult(jobs.ClaimOutcome.ALREADY_SUCCEEDED)])
    def claim(job_id):
        assert job_id == lease.job_id
        events.append('claim')
        return next(outcomes)
    def finish(job_id, token):
        assert (job_id, token) == (lease.job_id, lease.lease_token)
        events.append('finish')
        return {'status': 'SUCCEEDED'}
    monkeypatch.setattr(jobs, 'claim_job_result', claim)
    monkeypatch.setattr(jobs, 'finish_job', finish)
    for _ in range(2):
        service.execute_job(str(lease.job_id), executor=lambda given: events.append(('execute', given)))
    assert events == ['claim', ('execute', lease), 'finish', 'claim']


@pytest.mark.parametrize('completion', [None, {'status': 'RETRY_WAIT'}])
def test_rejected_completion_requires_retry(monkeypatch, lease, completion):
    claimed(monkeypatch, lease)
    monkeypatch.setattr(jobs, 'finish_job', lambda *a, **kw: completion)
    with pytest.raises(service.JobRetryRequired, match='Completion'):
        service.execute_job(str(lease.job_id), executor=lambda _: None)


@pytest.mark.parametrize('failure_persistence', ['accepted', 'stale', 'error'])
def test_executor_failure_cannot_ack(monkeypatch, lease, failure_persistence):
    claimed(monkeypatch, lease)
    calls = []
    def finish(job_id, token, **kwargs):
        calls.append((job_id, token, kwargs))
        if failure_persistence == 'error':
            raise RuntimeError('database unavailable')
        return {'status': 'RETRY_WAIT'} if failure_persistence == 'accepted' else None
    def fail(_):
        raise ValueError('Executor failed')
    monkeypatch.setattr(jobs, 'finish_job', finish)
    with pytest.raises((ValueError, service.JobRetryRequired)):
        service.execute_job(str(lease.job_id), executor=fail)
    assert calls == [(lease.job_id, lease.lease_token, {
        'failure_code': 'PROCESSING_FAILED', 'retry': True, 'retry_delay_seconds': 30})]


def test_default_executor_explicitly_fails_closed(monkeypatch, lease):
    claimed(monkeypatch, lease)
    monkeypatch.setattr(jobs, 'finish_job', lambda *a, **kw: {'status': 'RETRY_WAIT'})
    with pytest.raises(NotImplementedError, match='not implemented'):
        service.execute_job(str(lease.job_id))


def test_completion_database_error_does_not_mark_execution_failed(monkeypatch, lease):
    claimed(monkeypatch, lease)
    calls = []
    def finish(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError('completion unavailable')
    monkeypatch.setattr(jobs, 'finish_job', finish)
    with pytest.raises(RuntimeError, match='completion unavailable'):
        service.execute_job(str(lease.job_id), executor=lambda _: None)
    assert calls == [{}]  # Side effects may have succeeded: do not blindly replay them.


@pytest.mark.parametrize('invalid', ['missing_lease', 'wrong_job', 'unknown_outcome'])
def test_invalid_claim_contract_fails_closed(monkeypatch, lease, invalid):
    result = jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED)
    if invalid == 'wrong_job':
        result = jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED,
                                 jobs.JobLease(uuid4(), lease.lease_token, lease.lease_expires_at, 1))
    elif invalid == 'unknown_outcome':
        result = jobs.ClaimResult('UNSUPPORTED')
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: result)
    with pytest.raises(service.JobRetryRequired):
        service.execute_job(str(lease.job_id), executor=lambda _: pytest.fail('Must not execute'))


def test_executor_must_return_none_synchronously(monkeypatch, lease):
    claimed(monkeypatch, lease)
    monkeypatch.setattr(jobs, 'finish_job', lambda *a, **kw: {'status': 'RETRY_WAIT'})
    with pytest.raises(TypeError, match='synchronously'):
        service.execute_job(str(lease.job_id), executor=lambda _: 'artificial success')
