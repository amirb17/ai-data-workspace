"""Step 411B: validation-only guard for existing claimed query reservations.

This does not claim, mint, persist or execute a reservation. The caller must
load its fresh, current-owner reservation using QueryGovernanceService and
re-resolve publication bindings at dispatch time. This is only a consistency
check; passing cannot authorize execution or prevent a subsequent race.
"""
from dataclasses import dataclass
from collections.abc import Mapping

from app.query_governance.providers.base import CompiledQuery, ExecutionPermit
from app.warehouse.sql_physical_binding import InternalDraftBinding
from app.warehouse.sql_query_preflight import inspect_warehouse_query_scope


@dataclass(frozen=True)
class WarehouseClaimCheck:
    reservation_id: int
    workspace_id: int
    publication_model_count: int
    claimed_reservation_consistent: bool = True
    execution_authorized: bool = False


def inspect_claimed_warehouse_reservation(
    *,
    workspace_id: int,
    owner_key: str,
    consumer: str,
    reservation: Mapping,
    binding: InternalDraftBinding,
    query: CompiledQuery,
) -> WarehouseClaimCheck:
    """Read-only scope check on a fresh, already claimed durable record.

    This method deliberately does not invoke a query provider, query estimate,
    claim, or release. The actual query gateway must independently enforce
    the reservation and freshness checks at the point of dispatch.
    """
    if (type(workspace_id) is not int or workspace_id < 1
            or not isinstance(owner_key, str) or not owner_key
            or consumer not in ("SQL", "ANALYTICS", "ASK", "METRIC")
            or not isinstance(reservation, Mapping)):
        raise ValueError("Valid owned workspace and reservation context required")
    if (reservation.get("status") != "EXECUTING"
            or reservation.get("workspace_id") != workspace_id
            or reservation.get("owner_key") != owner_key
            or reservation.get("consumer") != consumer
            or reservation.get("provider") != "BIGQUERY"):
        raise ValueError("Current claimed warehouse reservation required")
    if type(query) is not CompiledQuery:
        raise ValueError("Compiled query required")
    if (reservation.get("request_token") != query.request_token
            or reservation.get("intent_sha256") != query.intent_sha256):
        raise ValueError("Reservation request identity mismatch")

    permit=ExecutionPermit.from_claimed_reservation(reservation)
    checked=inspect_warehouse_query_scope(
        workspace_id=workspace_id,binding=binding,query=query,permit=permit
    )
    return WarehouseClaimCheck(
        reservation_id=permit.reservation_id,
        workspace_id=workspace_id,
        publication_model_count=checked.table_count,
    )
