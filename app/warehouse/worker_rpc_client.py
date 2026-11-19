"""Small authenticated loopback client for independent warehouse worker processes.

The API/orchestrator may hold RPC keys and endpoint ports, but NEVER worker
database passwords or Google service-account credentials. Calls are bound to
one duty and operation, signed uniquely and timeout-bounded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import json
import re
import ssl

from app.warehouse.worker_rpc_protocol import (
    DUTY_OPERATIONS, WorkerRpcRejected, seal,
)


@dataclass(frozen=True)
class WarehouseWorkerClient:
    duty: str
    port: int
    secret: bytes = field(repr=False)
    timeout_seconds: float = 5.0
    host: str = "127.0.0.1"
    tls_context: ssl.SSLContext | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if (self.duty not in DUTY_OPERATIONS or type(self.port) is not int
                or not 1024 <= self.port <= 65535
                or type(self.secret) is not bytes or len(self.secret) < 32
                or not isinstance(self.timeout_seconds,(int,float))
                or isinstance(self.timeout_seconds,bool)
                or not 0 < self.timeout_seconds <= 10):
            raise ValueError("REVIEWED_WORKER_CLIENT_REQUIRED")
        if self.host == "127.0.0.1":
            if self.tls_context is not None:
                raise ValueError("LOOPBACK_RPC_MUST_NOT_USE_REMOTE_TLS_MODE")
        elif (type(self.host) is not str
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\\.internal", self.host)
                or type(self.tls_context) is not ssl.SSLContext
                or self.tls_context.verify_mode != ssl.CERT_REQUIRED
                or not self.tls_context.check_hostname
                or self.tls_context.minimum_version < ssl.TLSVersion.TLSv1_2):
            raise ValueError("PRIVATE_MTLS_WORKER_ENDPOINT_REQUIRED")

    def invoke(self, operation: str, arguments: dict) -> dict:
        payload = seal(self.secret,duty=self.duty,operation=operation,
                       arguments=arguments)
        if self.host == "127.0.0.1":
            connection = http.client.HTTPConnection(
                self.host, self.port, timeout=self.timeout_seconds)
        else:
            connection = http.client.HTTPSConnection(
                self.host, self.port, timeout=self.timeout_seconds,
                context=self.tls_context)
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
            if response.status == 401:
                raise WorkerRpcRejected("WORKER_RPC_AUTHENTICATION_REJECTED")
            if response.status == 422:
                raise WorkerRpcRejected("WORKER_RPC_OPERATION_REJECTED")
            if response.status == 400:
                raise WorkerRpcRejected("WORKER_RPC_AUTH_OR_REQUEST_REJECTED")
            if response.status == 503:
                raise WorkerRpcRejected("WORKER_RPC_SERVER_OPERATION_UNAVAILABLE")
            if response.status != 200:
                raise WorkerRpcRejected("WORKER_RPC_UNEXPECTED_HTTP_STATUS")
            try:
                result=json.loads(raw)
            except (UnicodeDecodeError,ValueError):
                raise WorkerRpcRejected("WORKER_RPC_RESPONSE_INVALID") from None
            if (type(result) is not dict or result.get("ok") is not True
                    or type(result.get("result")) is not dict):
                raise WorkerRpcRejected("WORKER_RPC_RESPONSE_INVALID")
            return result["result"]
        except (OSError,TimeoutError,http.client.HTTPException,ssl.SSLError) as exc:
            # Do not disclose host/credentials/headers from transport errors.
            raise WorkerRpcRejected("WORKER_RPC_CONNECTION_FAILED") from None
        finally:
            connection.close()
