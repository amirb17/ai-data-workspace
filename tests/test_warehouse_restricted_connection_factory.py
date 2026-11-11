"""Offline tests: missing, admin and cross-duty database credentials fail closed."""
import pytest

from app.db.warehouse_restricted_connection import RestrictedWarehouseConnection


def test_rejects_developer_admin_fallback():
    with pytest.raises(ValueError, match="RESTRICTED"):
        RestrictedWarehouseConnection(
            duty="query_control",
            dsn="dbname=ai_data_workspace user=postgres host=localhost",
            login="postgres",
        )


def test_requires_duty_specific_direct_login():
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        RestrictedWarehouseConnection(
            duty="query_control",
            dsn="dbname=ai_data_workspace user=datarise_other host=localhost",
            login="datarise_wh_control",
        )


def test_rejects_connection_options_and_wrong_database():
    with pytest.raises(ValueError):
        RestrictedWarehouseConnection(
            duty="accounting",
            dsn="dbname=ai_data_workspace user=datarise_wh_account options='-c search_path=public'",
            login="datarise_wh_account",
        )
    with pytest.raises(ValueError):
        RestrictedWarehouseConnection(
            duty="accounting",
            dsn="dbname=postgres user=datarise_wh_account",
            login="datarise_wh_account",
        )


def test_valid_factory_is_unprivileged_until_real_direct_login_checks():
    factory = RestrictedWarehouseConnection(
        duty="recovery_attestation",
        dsn="dbname=ai_data_workspace user=datarise_wh_attest host=localhost",
        login="datarise_wh_attest",
    )
    assert factory.duty == "recovery_attestation"
    assert "datarise_wh_attest" in repr(factory)
    assert "host=localhost" not in repr(factory)
    with pytest.raises(ValueError):
        RestrictedWarehouseConnection(
            duty="one_process_all_duties",
            dsn="dbname=ai_data_workspace user=datarise_wh_attest",
            login="datarise_wh_attest",
        )
