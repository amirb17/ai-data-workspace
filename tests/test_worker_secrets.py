
import json
from urllib.parse import urlsplit, unquote

import pytest

from app.processing_worker.worker_secrets import load_worker_database_url


class FakeSecrets:
    def __init__(self, username="datarise_worker"):
        self.username = username

    def get_secret_value(self, *, SecretId):
        assert SecretId == "test-worker-secret"
        return {
            "SecretString": json.dumps({
                "username": self.username,
                "password": "example@password",
                "host": "db.example.amazonaws.com",
                "port": 5432,
                "dbname": "aiworkspace",
            })
        }


def test_worker_secret_is_loaded(monkeypatch):
    monkeypatch.setenv(
        "DATARISE_WORKER_DB_SECRET_ARN",
        "test-worker-secret",
    )

    url = load_worker_database_url(FakeSecrets())

    assert urlsplit(url).username == "datarise_worker"
    assert unquote(urlsplit(url).password) == "example@password"


def test_worker_secret_is_required(monkeypatch):
    monkeypatch.delenv(
        "DATARISE_WORKER_DB_SECRET_ARN",
        raising=False,
    )

    with pytest.raises(ValueError):
        load_worker_database_url(FakeSecrets())


def test_worker_rejects_verifier_credentials(monkeypatch):
    monkeypatch.setenv(
        "DATARISE_WORKER_DB_SECRET_ARN",
        "test-worker-secret",
    )

    with pytest.raises(ValueError, match="Invalid worker database secret"):
        load_worker_database_url(
            FakeSecrets(username="datarise_verifier")
        )


def test_worker_secret_rejects_malformed_response(monkeypatch):
    monkeypatch.setenv(
        "DATARISE_WORKER_DB_SECRET_ARN",
        "test-worker-secret",
    )

    class InvalidSecrets:
        def get_secret_value(self, *, SecretId):
            return {"SecretString": "{}"}

    with pytest.raises(ValueError):
        load_worker_database_url(InvalidSecrets())
