"""Safe default behavior of opt-in restricted PostgreSQL acceptance."""
import pytest
from tests import test_warehouse_restricted_login_acceptance_414f0b as acceptance


def test_default_skips_without_explicit_live_role_gate(monkeypatch):
    monkeypatch.delenv("RUN_RESTRICTED_DB_ACCEPTANCE",raising=False)
    with pytest.raises(pytest.skip.Exception,match="explicitly disabled"):
        acceptance._settings()


@pytest.mark.parametrize("role",[
    "postgres",
    "datarise_a",
    "datarise_reader;DROP",
    "DATARISE_READER",
])
def test_untrusted_role_names_refused_without_any_connection(monkeypatch,role):
    monkeypatch.setenv("RUN_RESTRICTED_DB_ACCEPTANCE","1")
    monkeypatch.setenv("RESTRICTED_DB_ACCEPTANCE_DSN","postgresql://example.invalid/db")
    monkeypatch.setenv("RESTRICTED_DB_EXPECTED_ROLE",role)
    monkeypatch.setenv("RESTRICTED_DB_ACCEPTANCE_SCHEMA","test_warehouse_acceptance")
    with pytest.raises(pytest.fail.Exception,match="requires reviewed DSN"):
        acceptance._settings()


@pytest.mark.parametrize("schema",[
    "public","warehouse_production",
    "test_warehouse_a;DROP","test_warehouse_",
])
def test_non_disposable_schema_rejected_before_connection(monkeypatch,schema):
    monkeypatch.setenv("RUN_RESTRICTED_DB_ACCEPTANCE","1")
    monkeypatch.setenv("RESTRICTED_DB_ACCEPTANCE_DSN","postgresql://example.invalid/db")
    monkeypatch.setenv("RESTRICTED_DB_EXPECTED_ROLE","datarise_warehouse_result_reader")
    monkeypatch.setenv("RESTRICTED_DB_ACCEPTANCE_SCHEMA",schema)
    with pytest.raises(pytest.fail.Exception,match="requires reviewed DSN"):
        acceptance._settings()


def test_valid_configuration_only_returns_values_and_never_opens_connection(monkeypatch):
    monkeypatch.setenv("RUN_RESTRICTED_DB_ACCEPTANCE","1")
    monkeypatch.setenv("RESTRICTED_DB_ACCEPTANCE_DSN","postgresql://example.invalid/db")
    monkeypatch.setenv("RESTRICTED_DB_EXPECTED_ROLE","datarise_warehouse_result_reader")
    monkeypatch.setenv("RESTRICTED_DB_ACCEPTANCE_SCHEMA","test_warehouse_acceptance")
    assert acceptance._settings()==(
        "postgresql://example.invalid/db",
        "datarise_warehouse_result_reader",
        "test_warehouse_acceptance",
    )
