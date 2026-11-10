"""Step 413E: read-only inspection of claimed warehouse query recovery.

The only provider call is recover(query, permit), which looks up an existing
BigQuery job by deterministic reservation identity. No new query is submitted,
no durable reservation is claimed/released and no usage is finalized.

This diagnostic is NOT an execution gateway. A subsequent settlement requires
independent owned-state and terminal-job validation in a separate boundary.
"""
from dataclasses import dataclass
from enum import StrEnum

from app.query_governance.providers.base import (
    CompiledQuery, ExecutionPermit, QueryRecovery, RecoveryState,
)
from app.services.query_governance_service import QueryGovernanceService


class WarehouseRecoveryInspectionState(StrEnum):
    NOT_FOUND_HELD = "NOT_FOUND_HELD"
    IN_PROGRESS_HELD = "IN_PROGRESS_HELD"
    SUCCEEDED_UNSETTLED = "SUCCEEDED_UNSETTLED"
    FAILED_UNSETTLED = "FAILED_UNSETTLED"


@dataclass(frozen=True)
class WarehouseRecoveryInspection:
    reservation_id: int
    state: WarehouseRecoveryInspectionState
    provider_job_id: str
    execution_authorized: bool = False
    reservation_released: bool = False
    accounting_finalized: bool = False


def inspect_claimed_warehouse_recovery(
    *,
    governance: QueryGovernanceService,
    query: CompiledQuery,
    reservation_id: int,
    provider,
) -> WarehouseRecoveryInspection:
    """Observe one existing job; never submit, claim, release or settle.

    Provider errors propagate and leave EXECUTING state untouched. Even a
    provider-reported terminal job does not release budget or finalize usage.
    """
    if (
        type(governance) is not QueryGovernanceService
        or type(query) is not CompiledQuery
        or query.compiler_version != 2
        or query.workspace_id != governance.workspace_id
        or type(reservation_id) is not int or reservation_id < 1
        or getattr(provider, "identity", None) != "BIGQUERY"
    ):
        raise ValueError("Owned claimed warehouse query recovery required")
    row = governance.reservation(reservation_id)
    if (
        row.get("status") != "EXECUTING"
        or row.get("reservation_id") != reservation_id
        or row.get("workspace_id") != governance.workspace_id
        or row.get("owner_key") != governance.owner_key
        or row.get("consumer") != "SQL"
        or row.get("provider") != "BIGQUERY"
        or row.get("request_token") != query.request_token
        or row.get("intent_sha256") != query.intent_sha256
    ):
        raise ValueError("Exact owned EXECUTING reservation required")
    permit = ExecutionPermit.from_claimed_reservation(row)
    permit.authorize(query, provider="BIGQUERY")
    observed = provider.recover(query, permit)
    if type(observed) is not QueryRecovery:
        raise ValueError("Typed provider recovery observation required")
    statuses = {
        RecoveryState.NOT_FOUND: WarehouseRecoveryInspectionState.NOT_FOUND_HELD,
        RecoveryState.IN_PROGRESS: WarehouseRecoveryInspectionState.IN_PROGRESS_HELD,
        RecoveryState.SUCCEEDED: WarehouseRecoveryInspectionState.SUCCEEDED_UNSETTLED,
        RecoveryState.FAILED: WarehouseRecoveryInspectionState.FAILED_UNSETTLED,
    }
    if observed.state not in statuses:
        raise ValueError("Unknown provider recovery state")
    # An unrelated result cannot be accepted as evidence, even for diagnostics.
    if (
        observed.state == RecoveryState.SUCCEEDED
        and observed.result is not None
        and observed.result.provider_job_id != observed.provider_job_id
    ):
        raise ValueError("Recovered job/result identity mismatch")
    return WarehouseRecoveryInspection(
        reservation_id=reservation_id,
        state=statuses[observed.state],
        provider_job_id=observed.provider_job_id,
    )
