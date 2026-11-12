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


def test_local_dryrun_estimator_uses_bounded_server_query_only():
    from types import SimpleNamespace
    from app.query_governance.types import EstimateConfidence
    from tools.warehouse_local_live_api import LocalDryRunEstimator

    class Client:
        def __init__(self):
            self.calls = []
        def query(self, sql, **kwargs):
            self.calls.append((sql, kwargs))
            return SimpleNamespace(total_bytes_processed=1024)

    cloud = Client()
    result = LocalDryRunEstimator(cloud).estimate(
        SimpleNamespace(sql="SELECT 1 FROM `datarise-511817.dr_dev_w18.foo`")
    )
    assert result.bytes_processed == 10 * 1024 * 1024
    assert result.confidence == EstimateConfidence.UPPER_BOUND
    assert len(cloud.calls) == 1
    assert cloud.calls[0][1]["retry"] is None
    assert cloud.calls[0][1]["job_retry"] is None
    assert cloud.calls[0][1]["job_config"].dry_run is True


def test_local_dryrun_rejects_non_select_without_provider_call():
    from types import SimpleNamespace
    from tools.warehouse_local_live_api import LocalDryRunEstimator
    class Forbidden:
        def query(self, *_args, **_kwargs):
            raise AssertionError("Provider must not be reached")
    with pytest.raises(ValueError, match="COMPILED_SELECT_REQUIRED"):
        LocalDryRunEstimator(Forbidden()).estimate(SimpleNamespace(sql="DELETE FROM users"))
