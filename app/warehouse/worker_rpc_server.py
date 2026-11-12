"""One-duty local warehouse worker process.

Start a separate OS process for each duty. The process receives only its own
PostgreSQL DSN and separate HMAC RPC secret from its private environment.
It binds 127.0.0.1 and never exposes database credentials or query/result
payloads. Existing repository functions validate durable attempt/lease state.

This transports limited metadata operations only. It does NOT wire the
BigQuery send/lookup/result SDK, does NOT activate the FastAPI query routes,
and must not be exposed on a public interface or remote reverse proxy.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
from uuid import UUID

from app.db.warehouse_restricted_connection import ROLE_BY_DUTY
from app.db.warehouse_canonical_worker_connection import CanonicalWarehouseWorkerConnection
from app.warehouse.worker_rpc_protocol import (
    DUTY_OPERATIONS, MAX_BODY, ReplayGuard, WorkerRpcRejected,
    bounded_id, bounded_lease_request, verify,
)
from tools.warehouse_runtime_role_acceptance import accept

_MAX_RESPONSE = 4096


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _serializable(record):
    from dataclasses import is_dataclass
    if not is_dataclass(record):
        raise WorkerRpcRejected("WORKER_RESULT_TYPE_INVALID")
    result = asdict(record)
    for key, value in result.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = _iso(value)
    return result


def execute_operation(duty: str, operation: str, args: dict):
    """Exact service dispatch. No arbitrary SQL or method names accepted."""
    if operation not in DUTY_OPERATIONS.get(duty, ()):
        raise WorkerRpcRejected("WORKER_OPERATION_FORBIDDEN")
    if operation == "health":
        if args != {}:
            raise WorkerRpcRejected("WORKER_HEALTH_ARGUMENTS_FORBIDDEN")
        from app.db.database import repository_transaction
        with repository_transaction() as conn:
            current, session, schemas = conn.execute(
                "SELECT current_user, session_user, current_schemas(true)"
            ).fetchone()
        # pg_catalog intentionally precedes public to resolve trusted
        # built-ins before canonical application tables; current_schema()
        # therefore returns pg_catalog, not public.
        if (current != ROLE_BY_DUTY[duty] or session != ROLE_BY_DUTY[duty]
                or tuple(schemas[:3]) != (
                    "pg_catalog", "public", "datarise_warehouse_dev"
                )):
            raise WorkerRpcRejected("WORKER_IDENTITY_OR_CANONICAL_PATH_INVALID")
        return {"duty": duty, "database_identity_verified": True,
                "query_execution_enabled": False}

    if duty == "recovery_attestation":
        from app.services import warehouse_sql_send_recovery_service as recovery
        if operation == "inspect_recovery":
            return _serializable(recovery.inspect_recovery(
                attempt_id=bounded_id(args)))
        if operation == "acquire_recovery":
            attempt,revision,seconds = bounded_lease_request(args)
            return _serializable(recovery.acquire_recovery(
                attempt_id=attempt, expected_revision=revision,
                lease_seconds=seconds))
    if duty == "accounting" and operation == "settle_terminal":
        from app.services.warehouse_sql_attested_accounting_service import (
            settle_verified_warehouse_terminal,
        )
        return _serializable(settle_verified_warehouse_terminal(
            attempt_id=bounded_id(args)))
    if duty == "result_authorization" and operation == "inspect_eligibility":
        from app.services.warehouse_sql_result_eligibility_service import (
            inspect_settled_result_eligibility,
        )
        return _serializable(inspect_settled_result_eligibility(
            attempt_id=bounded_id(args)))
    if duty == "provider_submission" and operation == "record_send_boundary":
        # Intentionally closed until control-side session/publication checks
        # and the provider-side atomic authorization fence have a reviewed
        # cross-process contract. HMAC authentication alone is NOT dispatch
        # authority. Never weaken the submission role by granting access to
        # users/auth_sessions/publication source tables.
        raise WorkerRpcRejected("PROVIDER_SEND_BOUNDARY_NOT_APPROVED")
        # No SQL or BigQuery job is accepted. The exact lease must still be
        # checked inside PostgreSQL before its send-unknown marker commits.
        from app.services import warehouse_sql_send_recovery_service as recovery
        if set(args) != {"lease", "expected_revision"} or type(args["lease"]) is not dict:
            raise WorkerRpcRejected("WORKER_LEASE_ARGUMENTS_INVALID")
        wire = args["lease"]
        if set(wire) != {"attempt_id","revision","generation","token",
                         "lease_until","job"} or type(wire["job"]) is not dict:
            raise WorkerRpcRejected("WORKER_LEASE_ARGUMENTS_INVALID")
        if set(wire["job"]) != {"project", "location", "job_id"}:
            raise WorkerRpcRejected("WORKER_JOB_ARGUMENTS_INVALID")
        attempt = bounded_id({"attempt_id":wire["attempt_id"]})
        expected = args["expected_revision"]
        if type(expected) is not int or expected < 1:
            raise WorkerRpcRejected("WORKER_REVISION_INVALID")
        if type(wire["revision"]) is not int or type(wire["generation"]) is not int:
            raise WorkerRpcRejected("WORKER_LEASE_ARGUMENTS_INVALID")
        lease = recovery.RecoveryLease(
            attempt_id=attempt,revision=wire["revision"],
            generation=wire["generation"],token=UUID(wire["token"]),
            lease_until=datetime.fromisoformat(wire["lease_until"]),
            job=recovery.JobIdentity(**wire["job"]))
        return _serializable(recovery.record_pinned_send_boundary_without_dispatch(
            lease=lease, expected_revision=expected))
    raise WorkerRpcRejected("WORKER_OPERATION_UNIMPLEMENTED")


def _rpc_key(value: str | None) -> bytes:
    if type(value) is not str:
        raise WorkerRpcRejected("WORKER_RPC_SECRET_MISSING")
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error):
        raise WorkerRpcRejected("WORKER_RPC_SECRET_INVALID") from None
    if len(key) < 32:
        raise WorkerRpcRejected("WORKER_RPC_SECRET_INVALID")
    return key


def create_server(*, duty: str, secret: bytes, port: int,
                  handler=execute_operation, boundary=None) -> ThreadingHTTPServer:
    if duty not in DUTY_OPERATIONS or type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("WORKER_DUTY_OR_PORT_INVALID")
    if len(secret) < 32:
        raise ValueError("WORKER_SECRET_INVALID")
    if not callable(boundary):
        raise ValueError("EXPLICIT_WORKER_DATABASE_BOUNDARY_REQUIRED")
    guard = ReplayGuard()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Never log Authorization/signature headers, request bodies,
            # URI values or exception details.
            pass

        def do_POST(self):
            if self.client_address[0] != "127.0.0.1" or self.path != "/internal/v1/operation":
                self.send_error(404)
                return
            raw_size = self.headers.get("Content-Length")
            if raw_size is None or not raw_size.isascii() or not raw_size.isdecimal():
                self.send_error(400)
                return
            size = int(raw_size)
            if size < 1 or size > MAX_BODY:
                self.send_error(413)
                return
            body = self.rfile.read(size)
            try:
                # Authentication errors must remain indistinguishable to
                # unauthenticated clients, but must not be conflated with a
                # *verified* request that failed inside the handler.
                operation, args = verify(
                    secret,duty=duty,body=body,
                    signature=self.headers.get("X-Datarise-Signature",""),
                    guard=guard)
            except (WorkerRpcRejected, ValueError, TypeError):
                status,encoded = 401,b'{"ok":false,"error":"AUTHENTICATION_REJECTED"}'
            else:
                try:
                    # DB role is process-bound and verified at startup.
                    with boundary():
                        response = {"ok": True, "result": handler(duty,operation,args)}
                    encoded = json.dumps(response,separators=(",",":"),
                                         allow_nan=False).encode("utf-8")
                    if len(encoded) > _MAX_RESPONSE:
                        raise WorkerRpcRejected("WORKER_RESPONSE_TOO_LARGE")
                    status = 200
                except (WorkerRpcRejected,ValueError,TypeError):
                    status,encoded = 422,b'{"ok":false,"error":"OPERATION_REJECTED"}'
                except Exception:
                    status,encoded = 503,b'{"ok":false,"error":"UNAVAILABLE"}'
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(encoded)))
            self.send_header("Cache-Control","no-store")
            self.end_headers()
            self.wfile.write(encoded)

    return ThreadingHTTPServer(("127.0.0.1",port),Handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("duty",choices=tuple(DUTY_OPERATIONS))
    parser.add_argument("--port",type=int,required=True)
    args=parser.parse_args()
    try:
        dsn=os.environ.get("DATARISE_WAREHOUSE_DUTY_DSN")
        if not dsn:
            raise WorkerRpcRejected("WORKER_DSN_MISSING")
        secret=_rpc_key(os.environ.get("DATARISE_WAREHOUSE_RPC_SECRET_B64"))
        # Verify direct login + exact grants before exposing the socket.
        accept(args.duty,dsn)
        # A process may bind to only its own duty, never alternate credentials.
        role=ROLE_BY_DUTY[args.duty]
        factory=CanonicalWarehouseWorkerConnection(
            duty=args.duty,dsn=dsn)
        server=create_server(duty=args.duty,secret=secret,port=args.port,
                             boundary=factory.boundary)
        print(json.dumps({"worker_started":True,"duty":args.duty,
                          "bind":"127.0.0.1","port":args.port,
                          "query_execution_enabled":False}),flush=True)
        server.serve_forever(poll_interval=0.5)
        return 0
    except Exception as exc:
        # Never echo connection strings, credentials or provider exceptions.
        print("WAREHOUSE_WORKER_START_BLOCKED:",type(exc).__name__,file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
