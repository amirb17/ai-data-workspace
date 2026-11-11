"""Small authenticated loopback client for independent warehouse worker processes.

The API/orchestrator may hold RPC keys and endpoint ports, but NEVER worker
database passwords or Google service-account credentials. Calls are bound to
one duty and operation, signed uniquely and timeout-bounded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json

from app.warehouse.worker_rpc_protocol import (
    DUTY_OPERATIONS, WorkerRpcRejected, seal,
)


@dataclass(frozen=True)
class WarehouseWorkerClient:
    duty: str
    port: int
    secret: bytes = field(repr=False)
    timeout_seconds: float = 5.0

    def __post_init__(self):
        if (self.duty not in DUTY_OPERATIONS or type(self.port) is not int
                or not 1024 <= self.port <= 65535
                or type(self.secret) is not bytes or len(self.secret) < 32
                or not isinstance(self.timeout_seconds,(int,float))
                or isinstance(self.timeout_seconds,bool)
                or not 0 < self.timeout_seconds <= 10):
            raise ValueError("REVIEWED_LOOPBACK_WORKER_CLIENT_REQUIRED")

    def invoke(self, operation: str, arguments: dict) -> dict:
        payload = seal(self.secret,duty=self.duty,operation=operation,
                       arguments=arguments)
        connection=http.client.HTTPConnection(
            "127.0.0.1",self.port,timeout=self.timeout_seconds)
        try:
            connection.request(
                "POST","/internal/v1/operation",
                body=payload.body,headers={
                    "Content-Type":"application/json",
                    "X-Datarise-Signature":payload.digest,
                    "Cache-Control":"no-store",
                })
            response=connection.getresponse()
            # Bounded response; never decode unchecked huge payloads.
            if response.length is not None and response.length > 4096:
                raise WorkerRpcRejected("WORKER_RPC_RESPONSE_OVERSIZED")
            raw=response.read(4097)
            if len(raw)>4096:
                raise WorkerRpcRejected("WORKER_RPC_RESPONSE_OVERSIZED")
            if response.status!=200:
                raise WorkerRpcRejected("WORKER_RPC_UNAVAILABLE_OR_REJECTED")
            try:
                result=json.loads(raw)
            except (UnicodeDecodeError,ValueError):
                raise WorkerRpcRejected("WORKER_RPC_RESPONSE_INVALID") from None
            if (type(result) is not dict or result.get("ok") is not True
                    or type(result.get("result")) is not dict):
                raise WorkerRpcRejected("WORKER_RPC_RESPONSE_INVALID")
            return result["result"]
        except (OSError,TimeoutError,http.client.HTTPException) as exc:
            # Do not disclose host/credentials/headers from transport errors.
            raise WorkerRpcRejected("WORKER_RPC_CONNECTION_FAILED") from None
        finally:
            connection.close()
