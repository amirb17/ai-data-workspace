"""414B internal attempt-handle issuer and durable retry resolver.

NOT an authenticated HTTP endpoint, proof of publication freshness, budget
reservation, execution permit, or BigQuery dispatch gateway. Trusted callers
must construct QueryGovernanceService from the authenticated session and
compute logical_intent_sha256 from reviewed server-side logical inputs.
"""
from dataclasses import dataclass
import secrets

from app.db.database import repository_transaction
from app.db import warehouse_query_attempt_repository as registry
from app.services.query_governance_service import QueryGovernanceService


@dataclass(frozen=True)
class WarehouseQueryAttempt:
    attempt_id: int
    attempt_handle: str
    request_token: str
    logical_intent_sha256: str
    reused: bool
    execution_authorized: bool = False
    reservation_created: bool = False


def _result(row,reused):
    return WarehouseQueryAttempt(
        attempt_id=row["attempt_id"],
        attempt_handle=row["attempt_handle"].strip(),
        request_token=row["request_token"].strip(),
        logical_intent_sha256=row["logical_intent_sha256"].strip(),
        reused=reused,
    )


def create_or_resume_warehouse_attempt(
    *,governance: QueryGovernanceService,
    logical_intent_sha256: str,
    attempt_handle: str | None = None,
) -> WarehouseQueryAttempt:
    """First call issues a server-generated handle; retry uses that handle.

    The caller MUST authenticate the handle's source separately; a handle
    is not a bearer authorization credential. Never infer permission to
    dispatch from a successful lookup.
    """
    if type(governance) is not QueryGovernanceService:
        raise ValueError("Authenticated governance boundary required")
    handle=secrets.token_hex(16) if attempt_handle is None else attempt_handle
    with repository_transaction() as conn:
        row,reused=registry.register_attempt(
            conn,workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,attempt_handle=handle,
            logical_intent_sha256=logical_intent_sha256,
        )
    return _result(row,reused)


def inspect_owned_warehouse_attempt(
    *,governance: QueryGovernanceService,attempt_handle: str,
) -> WarehouseQueryAttempt:
    if type(governance) is not QueryGovernanceService:
        raise ValueError("Authenticated governance boundary required")
    with repository_transaction() as conn:
        row=registry.read_attempt(
            conn,workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,attempt_handle=attempt_handle,
        )
    return _result(row,True)
