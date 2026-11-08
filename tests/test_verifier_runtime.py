
import json

import pytest

from app.lambda_worker.verifier_runtime import build_verifier
from app.storage.versioned_artifacts import MemoryVersionedClient


class FakeSecrets:
    def get_secret_value(self, *, SecretId):
        return {
            "SecretString": json.dumps({
                "database_url": "postgresql://verifier:example@localhost/test"
            })
        }


def test_verifier_runtime_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED", raising=False)
    monkeypatch.delenv("DATARISE_PROCESSING_READERS", raising=False)

    with pytest.raises(PermissionError):
        build_verifier()


def test_verifier_requires_separate_secret(monkeypatch):
    monkeypatch.setenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "true")
    monkeypatch.setenv("DATARISE_PROCESSING_READERS", "true")
    monkeypatch.setenv("DATARISE_ARTIFACT_BUCKET", "test-bucket")
    monkeypatch.delenv("DATARISE_VERIFIER_DB_SECRET_ARN", raising=False)

    with pytest.raises(ValueError):
        build_verifier()


def test_verifier_construction_uses_injected_dependencies(monkeypatch):
    monkeypatch.setenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "true")
    monkeypatch.setenv("DATARISE_PROCESSING_READERS", "true")
    monkeypatch.setenv("DATARISE_ARTIFACT_BUCKET", "test-bucket")
    monkeypatch.setenv(
        "DATARISE_VERIFIER_DB_SECRET_ARN",
        "test-verifier-secret",
    )

    verifier = build_verifier(
        s3_client=MemoryVersionedClient(),
        secrets_client=FakeSecrets(),
        connection_factory=lambda: None,
    )

    assert callable(verifier)
