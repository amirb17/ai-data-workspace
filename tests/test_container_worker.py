import json
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import uuid4

import pytest

from app.db import processing_job_repository as jobs
from app.processing_worker import execution, worker
from app.processing_worker.boundary import ExecutionBoundary, ExecutionStopped
from app.processing_worker.messages import parse_job_message


class FakeQueue:
    def __init__(self, messages, events=None):
        self.messages = list(messages)
        self.events = [] if events is None else events
        self.acked = []
        self.retried = []
        self.closed = False

    def receive_one(self, stop):
        self.events.append('receive')
        if not self.messages:
            stop.set()
            return None
        return self.messages.pop(0)

    def acknowledge(self, message):
        self.events.append('ack')
        self.acked.append(message.message_id)

    def retry(self, message):
        self.events.append('retry')
        self.retried.append(message.message_id)

    def close(self):
        self.closed = True
        self.events.append('close')


@pytest.fixture
def job(monkeypatch):
    job_id = uuid4()
    lease = jobs.JobLease(job_id, uuid4(), datetime.now(timezone.utc)+timedelta(seconds=120), 1)
    scope = jobs.JobExecutionScope(1, 2, 3, 4, 5, 'SILVER')
    state = {'status': 'QUEUED', 'lost': False, 'events': []}
    def claim(given):
        assert given == job_id
        state['events'].append('claim')
        if state['status'] == 'SUCCEEDED':
            return jobs.ClaimResult(jobs.ClaimOutcome.ALREADY_SUCCEEDED)
        state['status'] = 'RUNNING'
        return jobs.ClaimResult(jobs.ClaimOutcome.CLAIMED, lease)
    def finish(given, token, **kwargs):
        assert (given, token) == (job_id, lease.lease_token)
        if state['lost']:
            return None
        state['status'] = 'RETRY_WAIT' if kwargs else 'SUCCEEDED'
        state['events'].append('failure-commit' if kwargs else 'success-commit')
        return {'status': state['status']}
    monkeypatch.setattr(jobs, 'claim_job_result', claim)
    monkeypatch.setattr(jobs, 'get_execution_scope', lambda *a, **kw: None if state['lost'] else scope)
    monkeypatch.setattr(jobs, 'renew_job_lease', lambda *a, **kw: None if state['lost'] else lease)
    monkeypatch.setattr(jobs, 'finish_job', finish)
    return job_id, state


def message(job_id, identifier='one'):
    return worker.QueueMessage(identifier, json.dumps({'job_id': str(job_id)}), 'private-receipt-'+identifier)


@pytest.mark.parametrize('body', ['{}', 'null', '[]', '{broken', '{"job_id":"bad"}',
                                '{"job_id":123}', '{"job_id":null}',
                                '{"upload_id":1}', json.dumps({'job_id': '00000000-0000-4000-8000-000000000001', 'operation': 'GOLD'})])
def test_malformed_messages_defer_without_claim(body, monkeypatch):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: pytest.fail('Invalid messages must not reach jobs'))
    queue = FakeQueue([worker.QueueMessage('bad', body, 'secret')])
    worker.SerialWorker(queue).run()
    assert queue.acked == [] and queue.retried == ['bad'] and queue.closed


def test_parser_normalizes_uuid_without_scope_fields():
    job_id = uuid4()
    assert parse_job_message(json.dumps({'job_id': job_id.hex.upper()})) == str(job_id)


@pytest.mark.parametrize('outcome', [jobs.ClaimOutcome.BUSY, jobs.ClaimOutcome.NOT_DUE, jobs.ClaimOutcome.NOT_FOUND])
def test_nonclaim_outcomes_defer_without_execution(job, monkeypatch, outcome):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: jobs.ClaimResult(outcome))
    queue = FakeQueue([message(job[0])])
    worker.SerialWorker(queue, boundary_executor=lambda _: pytest.fail('No claim')).run()
    assert queue.acked == [] and queue.retried == ['one']


@pytest.mark.parametrize('outcome', [jobs.ClaimOutcome.ALREADY_SUCCEEDED,
                                   jobs.ClaimOutcome.TERMINAL_FAILED, jobs.ClaimOutcome.SOURCE_INELIGIBLE])
def test_durable_terminal_outcomes_ack_without_execution(job, monkeypatch, outcome):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: jobs.ClaimResult(outcome))
    queue = FakeQueue([message(job[0])])
    worker.SerialWorker(queue, boundary_executor=lambda _: pytest.fail('Terminal job')).run()
    assert queue.acked == ['one'] and not queue.retried


def test_duplicate_deliveries_execute_once_and_commit_before_ack(job):
    job_id, state = job
    queue = FakeQueue([message(job_id, 'one'), message(job_id, 'two')], state['events'])
    def execute(boundary):
        assert isinstance(boundary, ExecutionBoundary)
        state['events'].append('execute')
        boundary.checkpoint(renew=True)
    worker.SerialWorker(queue, boundary_executor=execute).run()
    assert queue.acked == ['one', 'two'] and not queue.retried
    assert state['events'] == ['receive', 'claim', 'execute', 'success-commit', 'ack',
                               'receive', 'claim', 'ack', 'receive', 'close']


def test_executor_failure_does_not_ack(job):
    def fail(_):
        raise RuntimeError('private processing detail')
    queue = FakeQueue([message(job[0])])
    worker.SerialWorker(queue, boundary_executor=fail).run()
    assert not queue.acked and queue.retried == ['one']
    assert job[1]['status'] == 'RETRY_WAIT'


def test_default_worker_fails_closed(job):
    queue = FakeQueue([message(job[0])])
    worker.SerialWorker(queue).run()
    assert not queue.acked and queue.retried == ['one'] and job[1]['status'] == 'RETRY_WAIT'


def test_lease_loss_prevents_completion(job):
    def lose(boundary):
        job[1]['lost'] = True
        with pytest.raises(ExecutionStopped):
            boundary.checkpoint()
    queue = FakeQueue([message(job[0])])
    worker.SerialWorker(queue, boundary_executor=lose).run()
    assert not queue.acked and queue.retried == ['one'] and job[1]['status'] == 'RUNNING'


def test_completion_persistence_failure_defers(job, monkeypatch):
    monkeypatch.setattr(jobs, 'finish_job', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('unavailable')))
    queue = FakeQueue([message(job[0])])
    worker.SerialWorker(queue, boundary_executor=lambda _: None).run()
    assert not queue.acked and queue.retried == ['one'] and job[1]['status'] == 'RUNNING'


def test_shutdown_before_poll_closes_without_receiving():
    stop = Event()
    stop.set()
    queue = FakeQueue([])
    worker.SerialWorker(queue, stop=stop).run()
    assert queue.events == ['close']


def test_shutdown_during_receive_defers_without_claim(job):
    stop = Event()
    queue = FakeQueue([])
    def receive(_):
        stop.set()
        return message(job[0])
    queue.receive_one = receive
    worker.SerialWorker(queue, stop=stop).run()
    assert queue.retried == ['one'] and not queue.acked and job[1]['status'] == 'QUEUED'


def test_shutdown_during_chunk_stops_next_checkpoint_and_poll(job):
    stop = Event()
    queue = FakeQueue([message(job[0]), message(job[0], 'unpolled')])
    def execute(boundary):
        stop.set()
        with pytest.raises(ExecutionStopped):
            boundary.checkpoint()
    worker.SerialWorker(queue, stop=stop, boundary_executor=execute).run()
    assert len(queue.messages) == 1 and queue.retried == ['one'] and not queue.acked and queue.closed


def test_expired_job_time_budget_defers_without_success(job):
    time = {'value': 0.0}
    def execute(_):
        time['value'] = 3601.0
    queue = FakeQueue([message(job[0])])
    worker.SerialWorker(queue, boundary_executor=execute, clock=lambda: time['value']).run()
    assert queue.retried == ['one'] and not queue.acked


def test_ack_failure_closes_and_duplicate_does_not_reexecute(job):
    queue = FakeQueue([message(job[0])])
    def ack(_):
        raise RuntimeError('ack unavailable')
    queue.acknowledge = ack
    calls = []
    with pytest.raises(RuntimeError, match='ack unavailable'):
        worker.SerialWorker(queue, boundary_executor=lambda _: calls.append('execute')).run()
    assert queue.closed and job[1]['status'] == 'SUCCEEDED'
    retry = FakeQueue([message(job[0])])
    worker.SerialWorker(retry, boundary_executor=lambda _: pytest.fail('Do not repeat successful work')).run()
    assert retry.acked == ['one'] and calls == ['execute']


def test_signal_handlers_request_shutdown_and_are_restored(monkeypatch):
    stop = Event()
    handlers = {}
    events = []
    def install(signum, handler):
        old = handlers.get(signum, 'old-handler')
        handlers[signum] = handler
        events.append((signum, handler))
        return old
    monkeypatch.setattr(worker.signal, 'signal', install)
    queue = FakeQueue([])
    def receive(_):
        handlers[worker.signal.SIGTERM](None, None)
        return None
    queue.receive_one = receive
    assert worker.main(queue, stop=stop) == 0
    assert stop.is_set() and queue.closed
    assert all(handler == 'old-handler' for handler in handlers.values())


def test_local_entrypoint_has_no_default_transport(monkeypatch):
    monkeypatch.setattr(jobs, 'claim_job_result', lambda _: pytest.fail('No configured transport'))
    assert worker.main() == 2


def test_lambda_compatibility_imports_share_one_implementation():
    from app.lambda_worker import service, execution_boundary
    assert service.execute_job is execution.execute_job
    assert execution_boundary.ExecutionBoundary is ExecutionBoundary


def test_sensitive_body_receipt_and_errors_not_logged(job, caplog):
    queue = FakeQueue([worker.QueueMessage('bad', 'private-invalid-body', 'private-receipt')])
    worker.SerialWorker(queue).run()
    assert 'private-invalid-body' not in caplog.text and 'private-receipt' not in caplog.text
    assert 'private-invalid-body' not in repr(worker.QueueMessage('bad', 'private-invalid-body', 'private-receipt'))


@pytest.mark.parametrize('operation', ['receive', 'retry'])
def test_transport_errors_stop_and_close_without_ack(job, operation):
    queue = FakeQueue([worker.QueueMessage('bad', '{}', 'receipt')])
    def unavailable(*args):
        raise RuntimeError('transport unavailable')
    if operation == 'receive':
        queue.receive_one = unavailable
    else:
        queue.retry = unavailable
    with pytest.raises(RuntimeError, match='transport unavailable'):
        worker.SerialWorker(queue).run()
    assert queue.closed and not queue.acked


def test_signal_setup_failure_closes_transport_and_restores_prior_handler(monkeypatch):
    queue = FakeQueue([])
    restored = []
    def install(signum, handler):
        if handler == 'previous':
            restored.append(signum)
            return
        if signum == worker.signal.SIGINT:
            raise ValueError('signal unavailable')
        return 'previous'
    monkeypatch.setattr(worker.signal, 'signal', install)
    with pytest.raises(ValueError, match='signal unavailable'):
        worker.main(queue)
    assert queue.closed and restored == [worker.signal.SIGTERM]
