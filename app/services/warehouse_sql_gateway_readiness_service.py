"""414D1 internal gateway readiness rehearsal; NO dispatch and NO claim.

This module cannot authenticate the origin of QueryGovernanceService. A
future HTTP gateway must derive the principal from verified server session,
not a caller-controlled owner_key, and independently attest compiled SQL.
A positive result only means durable PostgreSQL metadata looks consistent.
"""
from dataclasses import dataclass

from app.db.database import repository_transaction
from app.db import warehouse_query_attempt_repository as attempts
from app.db.warehouse_query_publication_inspection_repository import (
    inspect_linked_publications,
)
from app.services.query_governance_service import QueryGovernanceService


@dataclass(frozen=True)
class WarehouseGatewayReadiness:
    attempt_handle: str
    reservation_id: int
    state: str
    metadata_consistent: bool
    execution_authorized: bool = False
    provider_dispatched: bool = False
    reservation_claimed: bool = False


def inspect_gateway_readiness_without_dispatch(
    *, governance: QueryGovernanceService, attempt_handle: str,
) -> WarehouseGatewayReadiness:
    """Owned DB reads only; never compile, admit, claim or run a query."""
    if type(governance) is not QueryGovernanceService:
        raise ValueError("Trusted governance context required")

    with repository_transaction() as conn:
        original=attempts.read_attempt(
            conn,workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,attempt_handle=attempt_handle,
        )
        inspected=inspect_linked_publications(
            conn,workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,attempt_id=original["attempt_id"],
        )
        return WarehouseGatewayReadiness(
            attempt_handle=original["attempt_handle"].strip(),
            reservation_id=inspected.reservation_id,
            state=inspected.reason,
            metadata_consistent=inspected.metadata_consistent,
        )
