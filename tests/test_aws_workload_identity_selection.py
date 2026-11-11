"""No-cloud AWS identity boundary tests for local vs Lambda/ECS use."""
from unittest.mock import patch

import pytest

from app import config


def test_local_dev_uses_explicit_profile(monkeypatch):
    for key in ("AWS_EXECUTION_ENV", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                "AWS_CONTAINER_CREDENTIALS_FULL_URI"):
        monkeypatch.delenv(key, raising=False)
    with patch("boto3.Session") as session:
        config.get_boto3_session()
    session.assert_called_once_with(
        profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION
    )


@pytest.mark.parametrize("runtime", [
    {"AWS_EXECUTION_ENV": "AWS_Lambda_python3.13"},
    {"AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/example"},
])
def test_workload_uses_role_without_profile(monkeypatch, runtime):
    for key in ("AWS_EXECUTION_ENV", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_PROFILE",
                "AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in runtime.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AWS_REGION", config.AWS_REGION)
    monkeypatch.setenv("S3_BUCKET_NAME", config.S3_BUCKET_NAME)
    with patch("boto3.Session") as session:
        config.get_boto3_session()
    session.assert_called_once_with(region_name=config.AWS_REGION)


@pytest.mark.parametrize("override", [
    {"AWS_PROFILE": "ai-data-workspace"},
    {"AWS_REGION": "us-east-1"},
    {"S3_BUCKET_NAME": "wrong-bucket"},
    {"AWS_CONFIG_FILE": "/home/profile"},
])
def test_deployed_workload_rejects_profile_and_scope_drift(monkeypatch, override):
    for key in ("AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AWS_EXECUTION_ENV", "AWS_Lambda_python3.13")
    monkeypatch.setenv("AWS_REGION", config.AWS_REGION)
    monkeypatch.setenv("S3_BUCKET_NAME", config.S3_BUCKET_NAME)
    for key, val in override.items():
        monkeypatch.setenv(key, val)
    with patch("boto3.Session") as session:
        with pytest.raises(RuntimeError, match="EXPLICIT_AWS_WORKLOAD_CONFIGURATION_REQUIRED"):
            config.get_boto3_session()
        session.assert_not_called()
