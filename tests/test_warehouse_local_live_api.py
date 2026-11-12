"""Local live API startup guards; never contacts Google or PostgreSQL."""
from unittest.mock import patch

import pytest

from app.db import database
from tools.warehouse_local_live_api import _assert_local_development


def _local_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_MODE", "session")
    monkeypatch.setenv("DATARISE_PUBLIC_LAUNCH_MODE", "off")
    for name in (
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_KEYFILE_JSON",
        "GOOGLE_CREDENTIALS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_local_live_bridge_requires_loopback_and_session(monkeypatch):
    _local_env(monkeypatch)
    with patch.object(
        database, "DATABASE_URL",
        "postgresql://localhost/ai_data_workspace?user=postgres",
    ):
        _assert_local_development()


def test_never_starts_with_remote_postgres(monkeypatch):
    _local_env(monkeypatch)
    with patch.object(
        database, "DATABASE_URL",
        "postgresql://db.example.org/ai_data_workspace?user=postgres",
    ):
        with pytest.raises(RuntimeError, match="LOOPBACK_DEVELOPMENT_DATABASE_REQUIRED"):
            _assert_local_development()


def test_never_starts_in_production_or_public_launch(monkeypatch):
    _local_env(monkeypatch)
    with patch.object(
        database, "DATABASE_URL",
        "postgresql://localhost/ai_data_workspace?user=postgres",
    ):
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(RuntimeError, match="EXPLICIT_LOCAL_DEVELOPMENT_ENV_REQUIRED"):
            _assert_local_development()
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("DATARISE_PUBLIC_LAUNCH_MODE", "on")
        with pytest.raises(RuntimeError, match="PUBLIC_LAUNCH_MUST_BE_OFF"):
            _assert_local_development()


def test_never_uses_downloaded_service_account_keys(monkeypatch):
    _local_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "ignored.json")
    with patch.object(
        database, "DATABASE_URL",
        "postgresql://localhost/ai_data_workspace?user=postgres",
    ):
        with pytest.raises(RuntimeError, match="KEYFILE_CREDENTIAL_OVERRIDES_FORBIDDEN"):
            _assert_local_development()
