"""Worker identity health and routing acceptance without cloud execution."""
from unittest.mock import patch

import pytest

from app.services.warehouse_worker_runtime_config import configured_worker_router
from app.warehouse.worker_rpc_client import WarehouseWorkerClient
from app.warehouse.worker_rpc_protocol import WorkerRpcRejected, seal
from app.warehouse.worker_rpc_server import execute_operation


def _router():
    return configured_worker_router({
        "DATARISE_WH_SUBMIT_RPC_PORT":"19081",
        "DATARISE_WH_ATTEST_RPC_PORT":"19082",
        "DATARISE_WH_ACCOUNT_RPC_PORT":"19083",
        "DATARISE_WH_RESULTS_RPC_PORT":"19084",
        "DATARISE_WH_SUBMIT_RPC_SECRET_B64":"YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
        "DATARISE_WH_ATTEST_RPC_SECRET_B64":"YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmI=",
        "DATARISE_WH_ACCOUNT_RPC_SECRET_B64":"Y2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2M=",
        "DATARISE_WH_RESULTS_RPC_SECRET_B64":"ZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGQ=",
    })


def test_signed_health_is_allowed_but_does_not_enable_execution():
    sealed=seal(b"a"*32,duty="accounting",operation="health",arguments={})
    assert b'"operation":"health"' in sealed.body
    with pytest.raises(WorkerRpcRejected,match="FORBIDDEN"):
        seal(b"a"*32,duty="accounting",operation="execute_sql",arguments={})


def test_health_rejects_arguments_without_touching_db():
    with pytest.raises(WorkerRpcRejected,match="ARGUMENTS_FORBIDDEN"):
        execute_operation("accounting","health",{"sql":"SELECT 1"})


def test_router_requires_all_worker_identity_probes():
    router=_router()
    with patch.object(WarehouseWorkerClient,"invoke",
        lambda self,operation,args: {
            "duty":self.duty,"database_identity_verified":True,
            "query_execution_enabled":False
        }):
        checked=router.probe_workers()
    assert set(checked)=={
        "provider_submission","recovery_attestation","accounting",
        "result_authorization"
    }


def test_probe_rejects_false_identity():
    router=_router()
    with patch.object(WarehouseWorkerClient,"invoke",
        lambda self,operation,args: {
            "duty":self.duty,"database_identity_verified":False,
            "query_execution_enabled":False
        }):
        with pytest.raises(ValueError,match="IDENTITY_PROBE_FAILED"):
            router.probe_workers()


def test_health_accepts_catalog_first_canonical_worker_search_path():
    class Result:
        def __init__(self, value):
            self.value=value
        def fetchone(self):
            return self.value
    class Conn:
        def execute(self, query):
            assert "current_schemas(true)" in query
            return Result((
                "datarise_wh_account", "datarise_wh_account",
                ["pg_catalog", "public", "datarise_warehouse_dev"],
            ))
    from contextlib import contextmanager
    @contextmanager
    def transaction():
        yield Conn()
    with patch("app.db.database.repository_transaction", transaction):
        assert execute_operation("accounting","health",{})=={
            "duty":"accounting",
            "database_identity_verified":True,
            "query_execution_enabled":False,
        }


def test_health_rejects_incorrect_schema_order():
    class Result:
        def fetchone(self):
            return ("datarise_wh_account","datarise_wh_account",
                    ["pg_catalog","datarise_warehouse_dev","public"])
    class Conn:
        def execute(self, query):
            return Result()
    from contextlib import contextmanager
    @contextmanager
    def transaction():
        yield Conn()
    with patch("app.db.database.repository_transaction", transaction):
        with pytest.raises(WorkerRpcRejected, match="CANONICAL_PATH_INVALID"):
            execute_operation("accounting","health",{})
