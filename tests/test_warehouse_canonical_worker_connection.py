"""Offline boundary checks for the canonical, restricted warehouse worker."""
from unittest.mock import patch
import pytest

from app.db.warehouse_canonical_worker_connection import (
    CanonicalWarehouseWorkerConnection, verify_canonical_routing,
    WORKER_DUTIES, ISOLATED,
)


class Result:
    def __init__(self, one): self.one=one
    def fetchone(self): return self.one


class FakeConnection:
    def __init__(self, *, role="datarise_wh_account", create=False,
                 public_create=False, shadow=False):
        self.role=role
        self.create=create
        self.public_create=public_create
        self.shadow=shadow
    def execute(self, statement, args=()):
        if "current_user,session_user,current_database()" in statement:
            return Result((self.role,self.role,"ai_data_workspace",True,
                           self.create,self.public_create))
        if "FROM pg_class c JOIN pg_namespace n" in statement:
            return Result((1,) if self.shadow else None)
        raise AssertionError("unexpected SQL")


def test_each_worker_duty_has_canonical_scope():
    names={
        "provider_submission":"datarise_wh_submit",
        "recovery_attestation":"datarise_wh_attest",
        "accounting":"datarise_wh_account",
        "result_authorization":"datarise_wh_results",
    }
    assert set(names) == WORKER_DUTIES
    for duty,role in names.items():
        verify_canonical_routing(FakeConnection(role=role),duty)


def test_canonical_worker_rejects_admin_and_unexpected_public_create():
    for kwargs in (
        {"role":"postgres"}, {"create":True},
        {"public_create":True}, {"shadow":True},
    ):
        with pytest.raises(RuntimeError):
            verify_canonical_routing(FakeConnection(**kwargs),"accounting")


def test_query_control_cannot_be_misused_as_worker():
    with pytest.raises(RuntimeError,match="DUTY_FORBIDDEN"):
        verify_canonical_routing(FakeConnection(),"query_control")


def test_worker_connection_cannot_use_another_dutys_login():
    with pytest.raises(ValueError,match="IDENTITY_MISMATCH"):
        CanonicalWarehouseWorkerConnection(
            duty="accounting",
            dsn="host=localhost dbname=ai_data_workspace user=datarise_wh_submit",
        )


def test_connection_factory_explicitly_resolves_canonical_first():
    from app.db.warehouse_canonical_worker_connection import SEARCH_PATH
    assert SEARCH_PATH=="pg_catalog, public, datarise_warehouse_dev, pg_temp"
    assert ISOLATED=="datarise_warehouse_dev"
