"""Authenticated local transport for one warehouse worker per OS process.

This is NOT an HTTP-facing public API. A worker binds only 127.0.0.1 and
requires an independent high-entropy RPC secret. Messages are HMAC-SHA256
signed, timestamped, nonce-bound and restricted to fixed operation names.
Replay cache is per worker process; for multi-replica deployment use a shared
durable nonce store / private service mesh instead of this local transport.

Only small metadata is transported. No SQL, provider credentials, session
tokens, database DSNs, result rows, or arbitrary Python method names.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any

MAX_BODY = 4096
CLOCK_SKEW_SECONDS = 30
NONCE_BYTES = 16
_MAX_NONCES = 8192
DUTY_OPERATIONS = {
    "provider_submission": frozenset({"health", "record_send_boundary"}),
    "recovery_attestation": frozenset({"health", "inspect_recovery", "acquire_recovery"}),
    "accounting": frozenset({"health", "settle_terminal"}),
    "result_authorization": frozenset({"health", "inspect_eligibility"}),
}


class WorkerRpcRejected(ValueError):
    pass


def _canonical(payload: dict) -> bytes:
    if type(payload) is not dict:
        raise WorkerRpcRejected("RPC_OBJECT_REQUIRED")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_BODY:
        raise WorkerRpcRejected("RPC_PAYLOAD_TOO_LARGE")
    return raw


def _secret(value: bytes) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise WorkerRpcRejected("INDEPENDENT_RPC_SECRET_REQUIRED")
    return value


@dataclass(frozen=True)
class RpcEnvelope:
    body: bytes
    digest: str


def seal(secret: bytes, *, duty: str, operation: str, arguments: dict,
         now: int | None = None, nonce: str | None = None) -> RpcEnvelope:
    key = _secret(secret)
    if operation not in DUTY_OPERATIONS.get(duty, ()):
        raise WorkerRpcRejected("RPC_DUTY_OPERATION_FORBIDDEN")
    message = {
        "version": 1,
        "duty": duty,
        "operation": operation,
        "arguments": arguments,
        "timestamp": int(time.time()) if now is None else now,
        "nonce": secrets.token_hex(NONCE_BYTES) if nonce is None else nonce,
    }
    body = _canonical(message)
    return RpcEnvelope(body, hmac.new(key, body, hashlib.sha256).hexdigest())


class ReplayGuard:
    def __init__(self):
        self._seen: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.Lock()

    def check_and_claim(self, nonce: str, now: int) -> None:
        with self._lock:
            for key, timestamp in tuple(self._seen.items()):
                if now - timestamp > CLOCK_SKEW_SECONDS * 2:
                    del self._seen[key]
            if nonce in self._seen:
                raise WorkerRpcRejected("RPC_REPLAY_REJECTED")
            if len(self._seen) >= _MAX_NONCES:
                raise WorkerRpcRejected("RPC_REPLAY_CACHE_FULL")
            self._seen[nonce] = now


def verify(secret: bytes, *, duty: str, body: bytes, signature: str,
           guard: ReplayGuard, now: int | None = None) -> tuple[str, dict]:
    key = _secret(secret)
    if type(body) is not bytes or len(body) > MAX_BODY:
        raise WorkerRpcRejected("RPC_BODY_INVALID")
    if type(signature) is not str or len(signature) != 64:
        raise WorkerRpcRejected("RPC_SIGNATURE_INVALID")
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WorkerRpcRejected("RPC_SIGNATURE_INVALID")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise WorkerRpcRejected("RPC_JSON_INVALID") from None
    if type(payload) is not dict or _canonical(payload) != body:
        raise WorkerRpcRejected("RPC_CANONICAL_ENCODING_REQUIRED")
    timestamp = payload.get("timestamp")
    nonce = payload.get("nonce")
    operation = payload.get("operation")
    clock = int(time.time()) if now is None else now
    if (payload.get("version") != 1 or payload.get("duty") != duty
            or operation not in DUTY_OPERATIONS.get(duty, ())
            or type(timestamp) is not int
            or abs(clock - timestamp) > CLOCK_SKEW_SECONDS
            or type(nonce) is not str or len(nonce) != NONCE_BYTES * 2
            or any(ch not in "0123456789abcdef" for ch in nonce)
            or type(payload.get("arguments")) is not dict):
        raise WorkerRpcRejected("RPC_ENVELOPE_INVALID")
    guard.check_and_claim(nonce, clock)
    return operation, payload["arguments"]


def bounded_id(arguments: dict) -> int:
    if set(arguments) != {"attempt_id"}:
        raise WorkerRpcRejected("RPC_ARGUMENTS_INVALID")
    attempt = arguments["attempt_id"]
    if type(attempt) is not int or not 1 <= attempt <= 2**63 - 1:
        raise WorkerRpcRejected("RPC_ATTEMPT_ID_INVALID")
    return attempt


def bounded_lease_request(arguments: dict) -> tuple[int, int, int]:
    if set(arguments) != {"attempt_id", "expected_revision", "lease_seconds"}:
        raise WorkerRpcRejected("RPC_ARGUMENTS_INVALID")
    attempt, revision, seconds = (arguments[k] for k in
                                  ("attempt_id", "expected_revision", "lease_seconds"))
    if (any(type(x) is not int for x in (attempt, revision, seconds))
            or not 1 <= attempt <= 2**63 - 1
            or not 1 <= revision <= 2**31 - 1
            or not 1 <= seconds <= 120):
        raise WorkerRpcRejected("RPC_LEASE_PARAMETERS_INVALID")
    return attempt, revision, seconds
