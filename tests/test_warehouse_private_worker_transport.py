"""Offline fail-closed contracts for independently deployed private warehouse RPC."""
from contextlib import nullcontext
from unittest.mock import patch
import ssl

import pytest

from app.warehouse.worker_rpc_client import WarehouseWorkerClient
from app.warehouse.worker_rpc_protocol import WorkerRpcRejected
from app.warehouse.worker_rpc_server import create_server

KEY = b"x" * 32


def secure_client_context():
    return ssl.create_default_context()


def test_private_worker_requires_verified_hostname_and_certificate_chain():
    ctx = secure_client_context()
    client = WarehouseWorkerClient(
        "accounting", 18443, KEY,
        host="accounting.warehouse.internal", tls_context=ctx,
    )
    assert client.host == "accounting.warehouse.internal"
    assert client.tls_context.verify_mode == ssl.CERT_REQUIRED
    assert client.tls_context.check_hostname is True


@pytest.mark.parametrize("host", [
    "localhost", "127.0.0.1.evil.com", "worker.example.com",
    "https://worker.warehouse.internal", "worker..internal",
    "worker.warehouse.internal:443", "10.0.1.2", "worker.warehouse.internal/path",
])
def test_private_worker_rejects_non_private_or_malformed_destinations(host):
    with pytest.raises(ValueError, match="PRIVATE_MTLS_WORKER_ENDPOINT_REQUIRED"):
        WarehouseWorkerClient(
            "accounting", 18443, KEY, host=host,
            tls_context=secure_client_context(),
        )


def test_remote_plaintext_and_unverified_tls_are_rejected():
    with pytest.raises(ValueError, match="PRIVATE_MTLS_WORKER_ENDPOINT_REQUIRED"):
        WarehouseWorkerClient(
            "accounting", 18443, KEY,
            host="worker.warehouse.internal",
        )
    ctx = ssl._create_unverified_context()
    with pytest.raises(ValueError, match="PRIVATE_MTLS_WORKER_ENDPOINT_REQUIRED"):
        WarehouseWorkerClient(
            "accounting", 18443, KEY,
            host="worker.warehouse.internal", tls_context=ctx,
        )
    ctx = secure_client_context()
    ctx.check_hostname = False
    with pytest.raises(ValueError, match="PRIVATE_MTLS_WORKER_ENDPOINT_REQUIRED"):
        WarehouseWorkerClient(
            "accounting", 18443, KEY,
            host="worker.warehouse.internal", tls_context=ctx,
        )


def test_loopback_contract_cannot_silently_be_upgraded_with_tls_context():
    with pytest.raises(ValueError, match="LOOPBACK_RPC_MUST_NOT_USE_REMOTE_TLS_MODE"):
        WarehouseWorkerClient("accounting", 18084, KEY,
                              tls_context=secure_client_context())


def test_client_uses_https_for_private_rpc_and_preserves_signed_envelope():
    ctx = secure_client_context()
    called = {}

    class Reply:
        length = 40
        status = 200

        def read(self, n):
            assert n == 4097
            return b'{"ok":true,"result":{"database_identity_verified":true}}'

    class Connection:
        def __init__(self, host, port, timeout, context):
            called.update(host=host, port=port, timeout=timeout, context=context)

        def request(self, method, endpoint, body, headers):
            called.update(method=method, endpoint=endpoint, body=body,
                          signature=headers["X-Datarise-Signature"])

        def getresponse(self):
            return Reply()

        def close(self):
            called["closed"] = True

    with patch("app.warehouse.worker_rpc_client.http.client.HTTPSConnection", Connection):
        result = WarehouseWorkerClient(
            "accounting", 18443, KEY,
            host="accounting.warehouse.internal", tls_context=ctx,
        ).invoke("health", {})
    assert result["database_identity_verified"] is True
    assert called["host"] == "accounting.warehouse.internal"
    assert called["context"] is ctx
    assert called["method"] == "POST"
    assert called["endpoint"] == "/internal/v1/operation"
    assert len(called["signature"]) == 64
    assert called["closed"] is True


def test_private_server_never_starts_without_mutual_tls():
    with pytest.raises(ValueError, match="PRIVATE_WORKER_MUTUAL_TLS_REQUIRED"):
        create_server(duty="accounting", secret=KEY, port=18444,
                      boundary=nullcontext, bind_host="0.0.0.0")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    with pytest.raises(ValueError, match="PRIVATE_WORKER_MUTUAL_TLS_REQUIRED"):
        create_server(duty="accounting", secret=KEY, port=18444,
                      boundary=nullcontext, bind_host="0.0.0.0",
                      tls_context=ctx)
    with pytest.raises(ValueError, match="PRIVATE_WORKER_MUTUAL_TLS_REQUIRED"):
        create_server(duty="accounting", secret=KEY, port=18444,
                      boundary=nullcontext, bind_host="192.168.0.1",
                      tls_context=ctx)


def test_worker_private_rpc_still_rejects_unreviewed_operations():
    client = WarehouseWorkerClient(
        "accounting", 18443, KEY,
        host="accounting.warehouse.internal", tls_context=secure_client_context(),
    )
    with pytest.raises(WorkerRpcRejected, match="FORBIDDEN"):
        client.invoke("execute_sql", {"sql": "SELECT 1"})
