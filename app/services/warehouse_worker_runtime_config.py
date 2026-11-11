"""Explicit per-process RPC routing configuration for warehouse metadata workers.

The API process receives ONLY four loopback ports and four distinct HMAC
signing secrets. PostgreSQL passwords, service account credentials, SQL, and
provider job payloads remain solely in the respective worker processes.

No defaults and no automatic activation: configuration is complete only when
all four endpoints and secrets are present and distinct. This does not build a
BigQuery runtime, override get_query_runtime(), or grant query execution.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping

from app.services.warehouse_worker_router import WarehouseWorkerRouter
from app.warehouse.worker_rpc_client import WarehouseWorkerClient

_PORT_KEY = {
    "provider_submission": "DATARISE_WH_SUBMIT_RPC_PORT",
    "recovery_attestation": "DATARISE_WH_ATTEST_RPC_PORT",
    "accounting": "DATARISE_WH_ACCOUNT_RPC_PORT",
    "result_authorization": "DATARISE_WH_RESULTS_RPC_PORT",
}
_SECRET_KEY = {
    "provider_submission": "DATARISE_WH_SUBMIT_RPC_SECRET_B64",
    "recovery_attestation": "DATARISE_WH_ATTEST_RPC_SECRET_B64",
    "accounting": "DATARISE_WH_ACCOUNT_RPC_SECRET_B64",
    "result_authorization": "DATARISE_WH_RESULTS_RPC_SECRET_B64",
}
_FORBIDDEN_ENV = frozenset({
    "DATARISE_WAREHOUSE_DUTY_DSN",
    "DATARISE_WAREHOUSE_RPC_SECRET_B64",
})


def _decode_secret(value: str | None) -> bytes:
    if type(value) is not str:
        raise ValueError("WAREHOUSE_RPC_SECRET_NOT_CONFIGURED")
    try:
        secret = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("WAREHOUSE_RPC_SECRET_INVALID") from None
    if len(secret) < 32:
        raise ValueError("WAREHOUSE_RPC_SECRET_TOO_SHORT")
    return secret


def configured_worker_router(environment: Mapping[str, str]) -> WarehouseWorkerRouter:
    """Construct a router without performing network IO or reading any DB DSN.

    Explicitly reject worker-only credentials placed in the API environment;
    they belong in separate OS processes. The application may still use its
    own dedicated *query-control* DSN, validated elsewhere.
    """
    if not isinstance(environment, Mapping):
        raise ValueError("WAREHOUSE_RPC_CONFIGURATION_REQUIRED")
    if any(environment.get(key) for key in _FORBIDDEN_ENV):
        raise ValueError("WORKER_CREDENTIAL_LEAK_IN_API_ENVIRONMENT")
    clients=[]
    for duty in _PORT_KEY:
        raw_port=environment.get(_PORT_KEY[duty])
        if type(raw_port) is not str or not raw_port.isascii() or not raw_port.isdecimal():
            raise ValueError("WAREHOUSE_RPC_PORT_NOT_CONFIGURED")
        port=int(raw_port)
        secret=_decode_secret(environment.get(_SECRET_KEY[duty]))
        clients.append(WarehouseWorkerClient(duty=duty,port=port,secret=secret))
    return WarehouseWorkerRouter(*clients)
