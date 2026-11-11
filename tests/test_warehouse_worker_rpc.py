"""HMAC, replay and local one-duty RPC transport integration tests."""
import json
from threading import Thread

import pytest

from app.warehouse.worker_rpc_client import WarehouseWorkerClient
from app.warehouse.worker_rpc_protocol import (
    ReplayGuard, WorkerRpcRejected, bounded_id, seal, verify,
)
from app.warehouse.worker_rpc_server import create_server

KEY=b"a"*32
OTHER_KEY=b"b"*32
NONCE="ab"*16


def test_signed_worker_request_is_verified_once_only():
    envelope=seal(KEY,duty="accounting",operation="settle_terminal",
                  arguments={"attempt_id":18},now=100,nonce=NONCE)
    replay=ReplayGuard()
    operation,args=verify(KEY,duty="accounting",body=envelope.body,
                          signature=envelope.digest,guard=replay,now=100)
    assert operation=="settle_terminal"
    assert args=={"attempt_id":18}
    with pytest.raises(WorkerRpcRejected,match="REPLAY"):
        verify(KEY,duty="accounting",body=envelope.body,
               signature=envelope.digest,guard=replay,now=100)


def test_request_rejected_for_wrong_duty_or_key():
    envelope=seal(KEY,duty="accounting",operation="settle_terminal",
                  arguments={"attempt_id":18},now=100,nonce=NONCE)
    with pytest.raises(WorkerRpcRejected,match="SIGNATURE"):
        verify(OTHER_KEY,duty="accounting",body=envelope.body,
               signature=envelope.digest,guard=ReplayGuard(),now=100)
    with pytest.raises(WorkerRpcRejected,match="ENVELOPE"):
        verify(KEY,duty="result_authorization",body=envelope.body,
               signature=envelope.digest,guard=ReplayGuard(),now=100)


def test_sql_and_unknown_operations_are_unavailable():
    with pytest.raises(WorkerRpcRejected,match="FORBIDDEN"):
        seal(KEY,duty="accounting",operation="execute_sql",
             arguments={"query":"SELECT 1"})
    with pytest.raises(WorkerRpcRejected,match="FORBIDDEN"):
        seal(KEY,duty="result_authorization",operation="settle_terminal",
             arguments={"attempt_id":18})


def test_no_unbounded_argument_or_boolean_attempt_ids():
    with pytest.raises(WorkerRpcRejected,match="ATTEMPT_ID"):
        bounded_id({"attempt_id":True})
    with pytest.raises(WorkerRpcRejected,match="ARGUMENTS"):
        bounded_id({"attempt_id":18,"sql":"SELECT 1"})


def test_stale_signature_is_rejected():
    envelope=seal(KEY,duty="accounting",operation="settle_terminal",
                  arguments={"attempt_id":18},now=1,nonce=NONCE)
    with pytest.raises(WorkerRpcRejected,match="ENVELOPE"):
        verify(KEY,duty="accounting",body=envelope.body,
               signature=envelope.digest,guard=ReplayGuard(),now=100)


def test_worker_roundtrip_is_loopback_and_does_not_require_database():
    received=[]
    def fake(duty,operation,args):
        received.append((duty,operation,args))
        return {"attempt_id":bounded_id(args),"accounted_bytes":0}
    server=create_server(duty="accounting",secret=KEY,port=18084,handler=fake)
    worker=Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        client=WarehouseWorkerClient("accounting",18084,KEY)
        response=client.invoke("settle_terminal",{"attempt_id":18})
        assert response=={"attempt_id":18,"accounted_bytes":0}
        assert received==[("accounting","settle_terminal",{"attempt_id":18})]
        with pytest.raises(WorkerRpcRejected,match="FORBIDDEN"):
            client.invoke("execute_sql",{"sql":"SELECT 1"})
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_rpc_client_rejects_weak_secrets_and_unreviewed_ports():
    with pytest.raises(ValueError,match="REVIEWED"):
        WarehouseWorkerClient("accounting",80,KEY)
    with pytest.raises(ValueError,match="REVIEWED"):
        WarehouseWorkerClient("accounting",18084,b"weak")
