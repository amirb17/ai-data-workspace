"""414F5: compose session-bound, server-pinned, bounded fake first-page read.

Internal-only rehearsal. No endpoint, real BigQuery client or IO permit.
Reauthorize *after* external IO so in-flight logout/revocation cannot
release rows to the caller.
"""
from dataclasses import dataclass
from fastapi import Request

from app.services.warehouse_sql_authenticated_result_service import (
    inspect_authenticated_warehouse_result_without_fetch,
)
from app.services.warehouse_sql_first_page_service import (
    read_first_warehouse_page_server_pinned_simulated,
    FirstPageTransport,
)


@dataclass(frozen=True)
class AuthenticatedFirstPage:
    attempt_handle: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    provider_has_more: bool
    bytes_encoded: int
    classification: str = "AUTHENTICATED_BOUNDED_FIRST_PAGE_SIMULATED"
    provider_dispatched: bool = False
    execution_authorized: bool = False


def read_authenticated_first_warehouse_page_simulated(
    *, request: Request, workspace_id: int, attempt_handle: str,
    transport: FirstPageTransport,
) -> AuthenticatedFirstPage:
    """Only an original owner/session handle can see a validated first page.

    Authentication is required both before and after simulated provider IO.
    The lower-level service separately verifies original job accounting,
    publication schema, provider job scope and payload size. No caller SQL,
    schema, provider job identity or cursor parameters are accepted.
    """
    before=inspect_authenticated_warehouse_result_without_fetch(
        request=request, workspace_id=workspace_id,
        attempt_handle=attempt_handle,
    )
    # The internal handle is resolved under the same authenticated boundary.
    # Return no numeric IDs or provider credentials to the caller.
    from app.services.warehouse_sql_authenticated_result_service import (
        _authenticated_attempt_id,
    )
    attempt_id=_authenticated_attempt_id(
        request=request, workspace_id=workspace_id,
        attempt_handle=attempt_handle,
    )
    result=read_first_warehouse_page_server_pinned_simulated(
        attempt_id=attempt_id, transport=transport,
    )
    after=inspect_authenticated_warehouse_result_without_fetch(
        request=request, workspace_id=workspace_id,
        attempt_handle=attempt_handle,
    )
    if before != after:
        raise ValueError("AUTHENTICATED_RESULT_SCOPE_CHANGED_DURING_LOOKUP")
    return AuthenticatedFirstPage(
        attempt_handle,result.columns,result.rows,
        result.provider_has_more,result.bytes_encoded,
    )
