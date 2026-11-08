
"""Regression tests for the trusted verifier's security boundary."""

import json

import pytest

from app.lambda_worker.verifier_runtime import build_verifier
from app.processing_worker.runtime_db_secret import database_url_from_secret


def make_secret(username="datarise_verifier"):
    return json.dumps({
        "username": username,
        "password": "example@password",
        "host": "db.example.amazonaws.com",
        "port": 5432,
        "dbname": "aiworkspace",
    })


class FakeSecrets:
    def __init__(self, username="datarise_verifier"):
        self.username = username
        self.calls = 0

    def get_secret_value(self, *, SecretId):
        assert SecretId == "test-verifier-secret"
        self.calls += 1
        return {"SecretString": make_secret(self.username)}


def configure_verifier(monkeypatch):
    monkeypatch.setenv(
        "DATARISE_ARTIFACT_BUCKET", "test-bucket"
    )
    monkeypatch.setenv(
        "DATARISE_VERIFIER_DB_SECRET_ARN",
        "test-verifier-secret",
    )


def test_verifier_accepts_own_database_identity():
    url = database_url_from_secret(
        make_secret(),
        expected_role="datarise_verifier",
    )

    assert url.startswith("postgresql://datarise_verifier:")


def test_verifier_rejects_worker_database_identity():
    with pytest.raises(ValueError, match="role mismatch"):
        database_url_from_secret(
            make_secret("datarise_worker"),
            expected_role="datarise_verifier",
        )


def test_verifier_rejects_disabled_deployment(monkeypatch):
    configure_verifier(monkeypatch)

    monkeypatch.setenv(
        "DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "false"
    )
    monkeypatch.setenv(
        "DATARISE_PROCESSING_READERS", "true"
    )

    secrets_client = FakeSecrets()

    with pytest.raises(PermissionError, match="not approved"):
        build_verifier(secrets_client=secrets_client)

    assert secrets_client.calls == 0


def test_verifier_rejects_disabled_readers(monkeypatch):
    configure_verifier(monkeypatch)

    monkeypatch.setenv(
        "DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "true"
    )
    monkeypatch.setenv(
        "DATARISE_PROCESSING_READERS", "false"
    )

    secrets_client = FakeSecrets()

    with pytest.raises(PermissionError, match="readers are not approved"):
        build_verifier(secrets_client=secrets_client)

    assert secrets_client.calls == 0

def test_enabled_verifier_loads_own_secret_and_uses_tls(monkeypatch):
    import app.lambda_worker.verifier_runtime as runtime

    configure_verifier(monkeypatch)
    monkeypatch.setenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "true")
    monkeypatch.setenv("DATARISE_PROCESSING_READERS", "true")

    secrets_client = FakeSecrets()
    calls = {}

    def fake_secure_conninfo(url, ca_path):
        calls["url"] = url
        calls["ca_path"] = ca_path
        return "verified-test-connection"

    def fake_connect(conninfo):
        calls["connection"] = conninfo
        return "fake-db-connection"

    class FakeRegistryVerifier:
        def __init__(self, storage, connection_factory, isolated_test):
            calls["isolated_test"] = isolated_test
            calls["connection_factory"] = connection_factory

        def verify(self, candidate_id):
            return self._connection_factory(candidate_id)

    monkeypatch.setattr(runtime, "secure_conninfo", fake_secure_conninfo)
    monkeypatch.setattr(runtime.psycopg, "connect", fake_connect)
    monkeypatch.setattr(runtime, "RegistryVerifier", FakeRegistryVerifier)

    verifier = build_verifier(
        s3_client=object(),
        secrets_client=secrets_client,
    )

    assert callable(verifier)
    assert secrets_client.calls == 1
    assert calls["url"].startswith("postgresql://datarise_verifier:")
    assert calls["ca_path"] == "/opt/certs/global-bundle.pem"
    assert calls["isolated_test"] is False

    assert calls["connection_factory"]() == "fake-db-connection"
    assert calls["connection"] == "verified-test-connection"


def test_enabled_verifier_rejects_worker_secret(monkeypatch):
    configure_verifier(monkeypatch)
    monkeypatch.setenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "true")
    monkeypatch.setenv("DATARISE_PROCESSING_READERS", "true")

    secrets_client = FakeSecrets(username="datarise_worker")

    with pytest.raises(ValueError, match="role mismatch"):
        build_verifier(
            s3_client=object(),
            secrets_client=secrets_client,
        )

    assert secrets_client.calls == 1
