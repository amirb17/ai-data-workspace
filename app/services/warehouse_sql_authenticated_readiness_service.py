"""414D2: authenticated session-only entry to non-executing readiness.

Caller supplies an ASGI Request, workspace id and server-issued handle.
Never accept client-supplied principals, provider objects, SQL, token, or
authorization flags. This is not an HTTP route and never dispatches.
"""
from fastapi import HTTPException, Request

from app.auth.config import require_secret
from app.auth import service as auth_service
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_gateway_readiness_service import (
    WarehouseGatewayReadiness, inspect_gateway_readiness_without_dispatch,
)


def inspect_authenticated_warehouse_gateway_readiness(
    *, request: Request, workspace_id: int, attempt_handle: str,
) -> WarehouseGatewayReadiness:
    """Resolve current authenticated session before any owned DB inspection."""
    if not isinstance(request, Request):
        raise ValueError("ASGI request is required")
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Positive workspace id required")
    if type(attempt_handle) is not str or not attempt_handle:
        raise ValueError("Server-issued attempt handle required")

    try:
        settings=require_secret()
    except ValueError as exc:
        raise HTTPException(
            status_code=503,detail="Warehouse session authentication unavailable",
        ) from exc
    if settings.mode!="session":
        raise HTTPException(
            status_code=503,detail="Warehouse gateway requires session authentication",
        )

    principal=auth_service.session(request.cookies.get(settings.session_cookie))
    if (not isinstance(principal,dict)
            or type(principal.get("user_id")) is not int
            or principal["user_id"] < 1
            or type(principal.get("owner_key")) is not str
            or not principal["owner_key"]):
        raise HTTPException(status_code=401,detail="Sign in to continue.")

    governance=QueryGovernanceService(workspace_id,principal)
    return inspect_gateway_readiness_without_dispatch(
        governance=governance,attempt_handle=attempt_handle,
    )
