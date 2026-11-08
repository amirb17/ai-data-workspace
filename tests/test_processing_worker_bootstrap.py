
import pytest

from app.processing_worker.bootstrap import build_worker
from app.processing_worker.integrated_executor import IntegratedExecutor
from app.processing_worker.sqs_transport import SqsTransport
from app.processing_worker.worker import SerialWorker


class FakeSqsClient:
    pass


def make_args():
    return {
        "sqs_client": FakeSqsClient(),
        "queue_url": "https://sqs.ap-south-1.amazonaws.com/123456789012/test",
        "storage_factory": lambda checkpoint: None,
        "verifier": lambda candidate_id: None,
    }


def test_worker_disabled_without_approval(monkeypatch):
    monkeypatch.delenv(
        "DATARISE_PROCESSING_DEPLOYMENT_APPROVED", raising=False
    )
    monkeypatch.delenv("DATARISE_PROCESSING_READERS", raising=False)

    with pytest.raises(PermissionError):
        build_worker(**make_args())


def test_worker_requires_reader_approval(monkeypatch):
    monkeypatch.setenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "true")
    monkeypatch.delenv("DATARISE_PROCESSING_READERS", raising=False)

    with pytest.raises(PermissionError):
        build_worker(**make_args())


def test_worker_constructs_existing_executor(monkeypatch):
    monkeypatch.setenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "true")
    monkeypatch.setenv("DATARISE_PROCESSING_READERS", "true")

    worker = build_worker(**make_args())

    assert isinstance(worker, SerialWorker)
    assert isinstance(worker.transport, SqsTransport)
    assert isinstance(worker.boundary_executor, IntegratedExecutor)
    assert worker.boundary_executor.enabled is True
    assert worker.boundary_executor.isolated_test is False


def test_worker_requires_verifier(monkeypatch):
    monkeypatch.setenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "true")
    monkeypatch.setenv("DATARISE_PROCESSING_READERS", "true")

    args = make_args()
    args["verifier"] = None

    with pytest.raises(TypeError):
        build_worker(**args)


def test_worker_rejects_invalid_visibility(monkeypatch):
    monkeypatch.setenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "true")
    monkeypatch.setenv("DATARISE_PROCESSING_READERS", "true")

    args = make_args()
    args["visibility_seconds"] = 901

    with pytest.raises(ValueError):
        build_worker(**args)
