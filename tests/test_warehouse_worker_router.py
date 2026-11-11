"""Typed cross-process worker router tests; no PostgreSQL or BigQuery."""
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from uuid import UUID
from unittest.mock import patch
import pytest

from app.warehouse.worker_rpc_client import WarehouseWorkerClient
from app.services.warehouse_worker_router import WarehouseWorkerRouter, _lease, _view


def _clients():
    return [
        WarehouseWorkerClient("provider_submission",19081,b"a"*32),
        WarehouseWorkerClient("recovery_attestation",19082,b"b"*32),
        WarehouseWorkerClient("accounting",19083,b"c"*32),
        WarehouseWorkerClient("result_authorization",19084,b"d"*32),
    ]


def test_router_requires_separate_duties_ports_and_secrets():
    clients=_clients()
    WarehouseWorkerRouter(*clients)
    with pytest.raises(ValueError,match="DISTINCT_WORKER_RPC_SECRETS"):
        WarehouseWorkerRouter(clients[0],clients[1],clients[2],
                              WarehouseWorkerClient("result_authorization",19084,b"a"*32))
    with pytest.raises(ValueError,match="DISTINCT_WORKER_PORTS"):
        WarehouseWorkerRouter(clients[0],clients[1],clients[2],
                              WarehouseWorkerClient("result_authorization",19083,b"d"*32))


def test_router_rejects_forged_or_untyped_recovery_views():
    with pytest.raises(ValueError,match="UNTRUSTED"):
        _view({"attempt_id":18})
    sample={
        "attempt_id":18,"revision":1,"phase":"INTENT_RECORDED",
        "classification":"RECOVERY_REQUIRED","revoked":False,
        "expired":False,"job":{"project":"p","location":"asia-south1","job_id":"j"}
    }
    assert _view(sample).attempt_id==18
    sample["revoked"]=1
    with pytest.raises(ValueError,match="UNTRUSTED"):
        _view(sample)


def test_worker_lease_requires_timezone_and_uuid():
    sample={
        "attempt_id":18,"revision":1,"generation":1,
        "token":str(UUID("00000000-0000-4000-8000-000000000001")),
        "lease_until":"2026-11-12T12:00:00+00:00",
        "job":{"project":"p","location":"asia-south1","job_id":"j"},
    }
    assert _lease(sample).job.job_id=="j"
    sample["lease_until"]="2026-11-12T12:00:00"
    with pytest.raises(ValueError,match="TIMEZONE"):
        _lease(sample)


def test_router_calls_accounting_worker_only_for_settlement():
    router=WarehouseWorkerRouter(*_clients())
    calls=[]
    def fake(client,op,args):
        calls.append((client.duty,op,args))
        return {
            "attempt_id":18, "reservation_id":20,"outcome":"SUCCEEDED",
            "accounted_bytes":50,"accounting_basis":"ACTUAL",
            "provider_job_id":"job","reused":False,
            "publication_release_authorized":False,"provider_dispatched":False,
        }
    with patch.object(WarehouseWorkerClient,"invoke",fake):
        result=router.settle_terminal(18)
    assert result.accounted_bytes==50
    assert calls==[("accounting","settle_terminal",{"attempt_id":18})]
