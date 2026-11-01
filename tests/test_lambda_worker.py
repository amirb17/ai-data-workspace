
import json

import pytest

from app.lambda_worker import handler


def sqs_record(message_id, body):
    return {
        "messageId": message_id,
        "eventSource": "aws:sqs",
        "body": json.dumps(body),
    }


def test_successful_job(monkeypatch):
    executed = []

    monkeypatch.setattr(
        handler,
        "execute_job",
        lambda job_id: executed.append(job_id),
    )

    event = {
        "Records": [
            sqs_record("m1", {"job_id": "00000000-0000-4000-8000-000000000001"})
        ]
    }

    result = handler.lambda_handler(event, None)

    assert result == {"batchItemFailures": []}
    assert executed == ["00000000-0000-4000-8000-000000000001"]


def test_executor_failure_returns_message_id(monkeypatch):
    def fail(job_id):
        raise RuntimeError("Temporary database failure")

    monkeypatch.setattr(handler, "execute_job", fail)

    event = {
        "Records": [
            sqs_record("m1", {"job_id": "00000000-0000-4000-8000-000000000001"})
        ]
    }

    assert handler.lambda_handler(event, None) == {
        "batchItemFailures": [
            {"itemIdentifier": "m1"}
        ]
    }


def test_partial_batch_failure(monkeypatch):
    executed = []

    def executor(job_id):
        executed.append(job_id)
        if job_id == "00000000-0000-4000-8000-000000000002":
            raise RuntimeError("Processing failed")

    monkeypatch.setattr(handler, "execute_job", executor)

    event = {
        "Records": [
            sqs_record("m1", {"job_id": "00000000-0000-4000-8000-000000000001"}),
            sqs_record("m2", {"job_id": "00000000-0000-4000-8000-000000000002"}),
            sqs_record("m3", {"job_id": "00000000-0000-4000-8000-000000000003"}),
        ]
    }

    result = handler.lambda_handler(event, None)

    assert result == {
        "batchItemFailures": [
            {"itemIdentifier": "m2"}
        ]
    }

    assert executed == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
        "00000000-0000-4000-8000-000000000003",
    ]


@pytest.mark.parametrize(
    "job_id",
    ["", " ", None, 123, [], {}, "not-a-uuid"],
)
def test_invalid_job_id_fails_safely(monkeypatch, job_id):
    def should_not_execute(_):
        pytest.fail("Executor must not run")

    monkeypatch.setattr(
        handler,
        "execute_job",
        should_not_execute,
    )

    event = {
        "Records": [
            sqs_record("m1", {"job_id": job_id})
        ]
    }

    assert handler.lambda_handler(event, None) == {
        "batchItemFailures": [
            {"itemIdentifier": "m1"}
        ]
    }


def test_malformed_json_fails_safely():
    event = {
        "Records": [
            {
                "messageId": "m1",
                "eventSource": "aws:sqs",
                "body": "{invalid-json}",
            }
        ]
    }

    assert handler.lambda_handler(event, None) == {
        "batchItemFailures": [
            {"itemIdentifier": "m1"}
        ]
    }


def test_empty_batch():
    assert handler.lambda_handler(
        {"Records": []},
        None,
    ) == {"batchItemFailures": []}


def test_missing_message_id_rejected():
    event = {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "body": json.dumps({"job_id": "00000000-0000-4000-8000-000000000001"}),
            }
        ]
    }

    with pytest.raises(
        ValueError,
        match="Expected an SQS record",
    ):
        handler.lambda_handler(event, None)


@pytest.mark.parametrize('body', [[], None, {}, {'upload_id': 1},
                                {'job_id': '00000000-0000-4000-8000-000000000001', 'upload_id': 1}])
def test_unsupported_message_body_never_executes(monkeypatch, body):
    monkeypatch.setattr(handler, 'execute_job', lambda _: pytest.fail('Must not execute'))
    assert handler.lambda_handler({'Records': [sqs_record('bad', body)]}, None) == {
        'batchItemFailures': [{'itemIdentifier': 'bad'}]}


@pytest.mark.parametrize('event', [None, [], {}, {'Records': {}}, {'Records': [None]},
                                 {'Records': [{'messageId': 'bad', 'eventSource': 'other'}]}])
def test_invalid_envelope_rejects_invocation(event):
    with pytest.raises(ValueError):
        handler.lambda_handler(event, None)


def test_exception_details_are_not_logged(monkeypatch, caplog):
    def fail(_):
        raise RuntimeError('sensitive-driver-or-executor-detail')
    monkeypatch.setattr(handler, 'execute_job', fail)
    assert handler.lambda_handler({'Records': [sqs_record('bad', {
        'job_id': '00000000-0000-4000-8000-000000000001'})]}, None)['batchItemFailures']
    assert 'sensitive-driver-or-executor-detail' not in caplog.text
