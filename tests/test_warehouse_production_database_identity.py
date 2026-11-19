"""Database identity acceptance is explicit, never inherited from DATABASE_URL.

This does not authorize provider dispatch or create a production DB role.
"""
import pytest

from app.db.warehouse_restricted_connection import (
    RestrictedWarehouseConnection, REVIEWED_DATABASES,
)
from app.db.warehouse_canonical_query_connection import (
    CanonicalWarehouseQueryControlConnection,
)


@pytest.mark.parametrize("database", ["ai_data_workspace", "aiworkspace"])
def test_reviewed_database_identities_accept_only_direct_named_roles(database):
    assert database in REVIEWED_DATABASES
    for duty, login in (
        ("query_control", "datarise_wh_control"),
        ("provider_submission", "datarise_wh_submit"),
        ("recovery_attestation", "datarise_wh_attest"),
        ("accounting", "datarise_wh_account"),
        ("result_authorization", "datarise_wh_results"),
    ):
        dsn = f"host=localhost port=5432 dbname={database} user={login} password=fake"
        assert RestrictedWarehouseConnection(duty, dsn, login).login == login
        if duty == "query_control":
            assert CanonicalWarehouseQueryControlConnection(dsn).dsn == dsn


@pytest.mark.parametrize("database", ["postgres", "template1", "aiworkspace_other"])
def test_unreviewed_database_is_rejected_before_network_connection(database):
    with pytest.raises(ValueError, match="RESTRICTED_WAREHOUSE_CONNECTION_IDENTITY_MISMATCH"):
        RestrictedWarehouseConnection(
            "provider_submission",
            f"host=localhost dbname={database} user=datarise_wh_submit password=fake",
            "datarise_wh_submit",
        )
    with pytest.raises(ValueError, match="RESTRICTED_WAREHOUSE_CONNECTION_IDENTITY_MISMATCH"):
        CanonicalWarehouseQueryControlConnection(
            f"host=localhost dbname={database} user=datarise_wh_control password=fake"
        )


def test_database_pin_does_not_allow_cross_duty_logins():
    with pytest.raises(ValueError, match="RESTRICTED_WAREHOUSE_LOGIN_REQUIRED"):
        RestrictedWarehouseConnection(
            "provider_submission",
            "dbname=aiworkspace user=datarise_wh_account password=fake",
            "datarise_wh_account",
        )
    with pytest.raises(ValueError, match="RESTRICTED_WAREHOUSE_CONNECTION_IDENTITY_MISMATCH"):
        CanonicalWarehouseQueryControlConnection(
            "dbname=aiworkspace user=datarise_api password=fake"
        )
