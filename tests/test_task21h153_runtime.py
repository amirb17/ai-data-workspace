"""Offline queue decisions and test-only Lambda boundary experiment."""
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4
import json

import pytest

from app.db import processing_job_repository as jobs
from app.lambda_worker import handler
from app.processing_worker import execution
from app.processing_worker.sqs_transport import SqsTransport
from app.processing_worker.worker import SerialWorker


class Queue:
    def __init__(self, body, stop):
        self.body, self.stop, self.events = body, stop, []
    def receive_message(self, **kw):
        return {'Messages': [dict(MessageId='synthetic', Body=self.body, ReceiptHandle='synthetic')]}
    def change_message_visibility(self, **kw):
        self.events.append(('retry', kw['VisibilityTimeout']))
        self.stop.set()
    def delete_message(self, **kw):
        self.events.append('ack')
        self.stop.set()
    def close(self):
        self.events.append('close')


@pytest.mark.parametrize('outcome', list(jobs.ClaimOutcome))
def test_actual_serial_worker_queue_decisions_without_unauthorized_execution(monkeypatch, outcome):
    job = uuid4()
    stop = Event()
    queue = Queue(json.dumps({'job_id': str(job)}), stop)
    lease = jobs.JobLease(job, uuid4(), datetime.now(timezone.utc)+timedelta(seconds=120), 1)
    result = jobs.ClaimResult(outcome, lease=lease if outcome == jobs.ClaimOutcome.CLAIMED else None)
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: result)
    # No executor supplied: CLAIMED must fail closed, persist failure and retry.
    failures = []
    monkeypatch.setattr(jobs, 'finish_job', lambda *args, **kw: failures.append(kw) or {'status': 'RETRY_WAIT'})
    SerialWorker(SqsTransport(queue, 'synthetic', enabled=True), stop=stop).run()
    terminal = outcome in {jobs.ClaimOutcome.ALREADY_SUCCEEDED, jobs.ClaimOutcome.TERMINAL_FAILED, jobs.ClaimOutcome.SOURCE_INELIGIBLE}
    assert queue.events == (['ack', 'close'] if terminal else [('retry', 30), 'close'])
    assert bool(failures) == (outcome == jobs.ClaimOutcome.CLAIMED)


@pytest.mark.parametrize('body', ['{}', '{', json.dumps({'job_id': str(uuid4()), 'command': 'forbidden'}), '[]'])
def test_poison_message_never_executes_or_silently_acknowledges(monkeypatch, body):
    stop = Event()
    queue = Queue(body, stop)
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: pytest.fail('poison message reached claim'))
    SerialWorker(SqsTransport(queue, 'synthetic', enabled=True), stop=stop).run()
    assert queue.events == [('retry', 30), 'close']
    # Redrive count/quarantine is an external deployment policy, not faked here.


@pytest.mark.parametrize('remaining,scope_available,completion', [(4000, True, True), (60000, False, True), (60000, True, False), (60000, True, True)])
def test_synthetic_lambda_invocation_uses_shared_boundary_and_commit_before_ack(monkeypatch, remaining, scope_available, completion):
    job = uuid4()
    lease = jobs.JobLease(job, uuid4(), datetime.now(timezone.utc)+timedelta(seconds=120), 1)
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED, lease=lease))
    monkeypatch.setattr(jobs, 'get_execution_scope', lambda *a, **kw: SimpleNamespace() if scope_available else None)
    monkeypatch.setattr(jobs, 'renew_job_lease', lambda *a, **kw: lease)
    events = []
    def finish(*a, **kw):
        events.append('failure' if kw else 'completion')
        return {'status': 'RETRY_WAIT' if kw else 'SUCCEEDED'} if completion else None
    monkeypatch.setattr(jobs, 'finish_job', finish)
    # Test-only injection into existing handler. No deployed handler, bootstrap,
    # verifier, AWS trigger or default executor is changed.
    monkeypatch.setattr(handler, 'execute_job', lambda identity: execution.execute_job(
        identity, boundary_executor=lambda b: events.append('execute'), remaining_time_ms=lambda: remaining))
    response = handler.lambda_handler({'Records': [dict(eventSource='aws:sqs', messageId='one', body=json.dumps({'job_id': str(job)}))]}, None)
    success = remaining > 5000 and scope_available and completion
    assert response == {'batchItemFailures': [] if success else [{'itemIdentifier': 'one'}]}
    assert ('execute' in events) == (remaining > 5000 and scope_available)
    if success:
        assert events == ['execute', 'completion']
