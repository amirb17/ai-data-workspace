"""414F5: authenticated, read-only warehouse result eligibility inspection.

No public API or result fetch. This prevents internal attempt IDs alone
from establishing access to settled query result metadata.
"""
from dataclasses import dataclass
import re

from fastapi import HTTPException, Request

from app.auth.config import require_secret
from app.db import warehouse_query_attempt_repository as attempts
from app.db import warehouse_query_send_repository as repo
from app.services.warehouse_sql_dispatch_checkpoint_service import _session_locked
from app.services.warehouse_sql_result_eligibility_service import (
    _inspect_settled_result_eligibility_locked,
)


@dataclass(frozen=True)
class AuthenticatedResultInspection:
    attempt_handle: str
    classification: str
    max_page_rows: int
    result_fetched: bool = False
    provider_dispatched: bool = False


def inspect_authenticated_warehouse_result_without_fetch(
    *, request: Request, workspace_id: int, attempt_handle: str,
) -> AuthenticatedResultInspection:
    """Verify the authenticated owner and original session before eligibility.

    Original checkpoint login-session provenance is required conservatively;
    an expired, replaced or revoked session never enables result access.
    """
    if (not isinstance(request, Request)
            or type(workspace_id) is not int or workspace_id < 1
            or type(attempt_handle) is not str
            or not re.fullmatch(r"[a-f0-9]{32}", attempt_handle)):
        raise ValueError("AUTHENTICATED_RESULT_HANDLE_REQUIRED")
    try:
        settings = require_secret()
    except ValueError:
        raise HTTPException(503, "Warehouse session unavailable") from None
    if settings.mode != "session":
        raise HTTPException(503, "Warehouse session required")
    token = request.cookies.get(settings.session_cookie)
    if not token or len(token) > 128:
        raise HTTPException(401, "Sign in to continue.")
    with repo.transaction(workspace_id,duty='result_authorization') as conn:
        user, session = _session_locked(conn, token)
        workspace = repo.one(
            conn, "SELECT owner,status FROM workspaces WHERE workspace_id=%s FOR SHARE",
            (workspace_id,),
        )
        if (not workspace or workspace["owner"] != user["owner_key"]
                or workspace["status"] != "ACTIVE"):
            raise HTTPException(403, "Workspace unavailable")
        attempt = attempts.read_attempt(
            conn, workspace_id=workspace_id, owner_key=user["owner_key"],
            attempt_handle=attempt_handle,
        )
        checkpoint = repo.checkpoint(conn, attempt["attempt_id"])
        if (checkpoint["workspace_id"] != workspace_id
                or checkpoint["owner_key"] != user["owner_key"]
                or checkpoint["user_id"] != user["user_id"]
                or checkpoint["session_digest"] != session["token_digest"]):
            raise HTTPException(403, "Original query session required")
        if repo.one(conn, """
            SELECT 1 FROM warehouse_query_dispatch_revocations
            WHERE attempt_id=%s LIMIT 1
        """, (attempt["attempt_id"],)):
            raise HTTPException(403, "Query result access revoked")
        eligible = _inspect_settled_result_eligibility_locked(
            conn, attempt["attempt_id"],
        )
        return AuthenticatedResultInspection(
            attempt_handle, "AUTHORIZED_RESULT_INSPECTION_NO_FETCH",
            eligible.max_page_rows,
        )


def _authenticated_attempt_id(
    *, request: Request, workspace_id: int, attempt_handle: str,
) -> int:
    """Trusted-only ID resolver; repeats session binding before simulated IO.

    No public endpoint should return this numeric identifier.
    """
    inspect_authenticated_warehouse_result_without_fetch(
        request=request, workspace_id=workspace_id,
        attempt_handle=attempt_handle,
    )
    settings=require_secret()
    token=request.cookies.get(settings.session_cookie)
    with repo.transaction(workspace_id,duty='result_authorization') as conn:
        user,session=_session_locked(conn,token)
        workspace=repo.one(
            conn,"SELECT owner,status FROM workspaces WHERE workspace_id=%s FOR SHARE",
            (workspace_id,),
        )
        if (not workspace or workspace["owner"]!=user["owner_key"]
                or workspace["status"]!="ACTIVE"):
            raise HTTPException(403,"Workspace unavailable")
        a=attempts.read_attempt(
            conn,workspace_id=workspace_id,owner_key=user["owner_key"],
            attempt_handle=attempt_handle,
        )
        c=repo.checkpoint(conn,a["attempt_id"])
        if (c["workspace_id"]!=workspace_id
                or c["owner_key"]!=user["owner_key"]
                or c["user_id"]!=user["user_id"]
                or c["session_digest"]!=session["token_digest"]
                or repo.one(conn,"SELECT 1 FROM warehouse_query_dispatch_revocations WHERE attempt_id=%s",(a["attempt_id"],))):
            raise HTTPException(403,"Original query result access unavailable")
        return a["attempt_id"]
