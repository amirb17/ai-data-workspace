"""414B: atomic, durable attempt identity. Never admit, claim or dispatch SQL.

The caller MUST obtain the handle and logical intent digest from an
authenticated server-owned logical request. Neither this module nor its
return value independently authorizes execution.
"""
import re
import secrets

from psycopg.rows import dict_row

from app.db import query_governance_repository as governance_repository

_HANDLE=re.compile(r"^[a-f0-9]{32}$")
_DIGEST=re.compile(r"^[a-f0-9]{64}$")


class WarehouseAttemptConflict(ValueError):
    pass


def register_attempt(
    conn, *, workspace_id: int, owner_key: str,
    attempt_handle: str, logical_intent_sha256: str,
):
    """Create exactly once, or read original attempt on same-handle retry.

    Under uniqueness, a simultaneous INSERT is serialized by PostgreSQL.
    Failed/foreign/conflicting retries cannot replace a request token.
    The token is generated only inside the trusted backend.
    """
    if (type(workspace_id) is not int or workspace_id < 1
            or not isinstance(owner_key,str) or not owner_key
            or type(attempt_handle) is not str
            or _HANDLE.fullmatch(attempt_handle) is None
            or type(logical_intent_sha256) is not str
            or _DIGEST.fullmatch(logical_intent_sha256) is None):
        raise ValueError("Trusted bounded warehouse attempt identity required")

    governance_repository._lock_scopes(conn,workspace_id)
    governance_repository._require_owner(conn,workspace_id,owner_key)

    token=secrets.token_hex(32)
    row=conn.cursor(row_factory=dict_row).execute(
        """
        INSERT INTO warehouse_query_attempt_registry
            (attempt_handle,request_token,workspace_id,owner_key,
             logical_intent_sha256)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (attempt_handle) DO NOTHING
        RETURNING *
        """,
        (attempt_handle,token,workspace_id,owner_key,logical_intent_sha256),
    ).fetchone()
    if row is not None:
        return row, False

    original=conn.cursor(row_factory=dict_row).execute(
        """SELECT * FROM warehouse_query_attempt_registry
           WHERE attempt_handle=%s""",
        (attempt_handle,),
    ).fetchone()
    if original is None:
        raise WarehouseAttemptConflict("WAREHOUSE_ATTEMPT_RETRY_UNCERTAIN")
    if (original["workspace_id"]!=workspace_id
            or original["owner_key"]!=owner_key):
        raise WarehouseAttemptConflict("WAREHOUSE_ATTEMPT_NOT_OWNED")
    if original["logical_intent_sha256"]!=logical_intent_sha256:
        raise WarehouseAttemptConflict("WAREHOUSE_ATTEMPT_INTENT_CONFLICT")
    return original, True


def read_attempt(
    conn, *, workspace_id: int, owner_key: str, attempt_handle: str,
):
    if (type(attempt_handle) is not str
            or _HANDLE.fullmatch(attempt_handle) is None):
        raise ValueError("Valid server-issued attempt handle required")
    governance_repository._lock_scopes(conn,workspace_id)
    governance_repository._require_owner(conn,workspace_id,owner_key)
    row=conn.cursor(row_factory=dict_row).execute(
        """SELECT * FROM warehouse_query_attempt_registry
           WHERE attempt_handle=%s AND workspace_id=%s AND owner_key=%s""",
        (attempt_handle,workspace_id,owner_key),
    ).fetchone()
    if row is None:
        raise WarehouseAttemptConflict("WAREHOUSE_ATTEMPT_NOT_FOUND")
    return row
