"""414D4b: bridge deterministic compiler identity to the server-issued token.

The compiler's deterministic request_token is a *fingerprint-derived hint*,
not the durable request identity. The attempt registry's cryptographically
random request_token owns retry identity. This helper recompiles the logical
draft and exact binding, then substitutes ONLY the server-issued token.
The source binding / attempt objects still require trusted database resolution
in a future gateway. This is not a query authorization or provider adapter.
"""
from dataclasses import dataclass, replace

from app.query_governance.providers.base import CompiledQuery
from app.services.warehouse_query_attempt_service import WarehouseQueryAttempt
from app.services.warehouse_sql_stored_draft_service import StoredWarehouseLogicalDraft
from app.warehouse.sql_physical_binding import InternalDraftBinding
from app.warehouse.sql_single_model_compiler import compile_single_model_sql


@dataclass(frozen=True)
class BoundWarehouseQueryIdentity:
    query: CompiledQuery
    attempt_id: int
    deterministic_compiler_token: str
    execution_authorized: bool = False
    provider_dispatched: bool = False
    reservation_created: bool = False


def bind_compiler_identity_to_attempt_without_dispatch(
    *, workspace_id: int, attempt: WarehouseQueryAttempt,
    stored_draft: StoredWarehouseLogicalDraft,
    binding: InternalDraftBinding,
) -> BoundWarehouseQueryIdentity:
    """Recompile an exact logical draft and use ONLY the stored attempt token."""
    if (type(workspace_id) is not int or workspace_id < 1
            or type(attempt) is not WarehouseQueryAttempt
            or type(stored_draft) is not StoredWarehouseLogicalDraft
            or type(binding) is not InternalDraftBinding
            or attempt.attempt_id != stored_draft.attempt_id
            or not stored_draft.persisted
            or attempt.execution_authorized
            or attempt.reservation_created
            or stored_draft.execution_authorized
            or stored_draft.provider_dispatched
            or binding.executable is not False):
        raise ValueError("Immutable owned attempt, logical draft and binding required")

    # The trusted compiler recomputes all physical SQL and its exact digest.
    compiled=compile_single_model_sql(
        workspace_id=workspace_id,draft=stored_draft.draft,binding=binding,
    )
    if compiled.compiler_version != 2:
        raise ValueError("Reviewed v2 warehouse compilation required")

    # A retry preserves the attempt's token even if another attempt compiled
    # the same query. The original compiler intent remains byte-for-byte.
    rebound=replace(compiled,request_token=attempt.request_token)
    assert rebound.intent_sha256 == compiled.intent_sha256
    assert rebound.sql == compiled.sql
    assert rebound.physical_tables == compiled.physical_tables
    return BoundWarehouseQueryIdentity(
        query=rebound,attempt_id=attempt.attempt_id,
        deterministic_compiler_token=compiled.request_token,
    )
