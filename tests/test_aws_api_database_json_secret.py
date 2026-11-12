"""Validate dedicated API secret-field connection behavior without opening sockets."""
from unittest.mock import patch
import pytest
from app.db import database


def test_api_json_secret_fields_connect_as_keywords(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", None)
    values = {
        "DATARISE_DB_HOST": "db.example.internal",
        "DATARISE_DB_PORT": "5432",
        "DATARISE_DB_NAME": "workspace",
        "DATARISE_DB_USERNAME": "api",
        "DATARISE_DB_PASSWORD": "pw:@/#? special",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with patch.object(database.psycopg, "connect", return_value="connection") as connect:
        assert database._api_database_connection() == "connection"
    connect.assert_called_once_with(
        host="db.example.internal", port="5432", dbname="workspace",
        user="api", password="pw:@/#? special",
    )


def test_incomplete_api_secret_fails_without_disclosing_password(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", None)
    for key in ("DATARISE_DB_HOST", "DATARISE_DB_PORT", "DATARISE_DB_NAME",
                "DATARISE_DB_USERNAME", "DATARISE_DB_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATARISE_DB_PASSWORD", "TOP_SECRET")
    with pytest.raises(RuntimeError, match="API database secret fields are incomplete") as exc:
        database._api_database_connection()
    assert "TOP_SECRET" not in str(exc.value)


def test_existing_database_url_remains_supported(monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", "postgresql://test.local/db")
    with patch.object(database.psycopg, "connect", return_value="connection") as connect:
        database._api_database_connection()
    connect.assert_called_once_with("postgresql://test.local/db")
