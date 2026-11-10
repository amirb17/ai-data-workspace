"""Step 413F: warehouse terminal job settlement through existing governance.

Never submit SQL, claim, re-admit, release, or make a new execution attempt.
All provider observations use the existing deterministic BigQuery recovery
contract. Durable completion/failure is exclusively implemented in the
existing PostgreSQL governance repository and is atomic/idempotent.

This is an internal recovery-only entry point, not a dispatch authorization.
"""
from dataclasses import dataclass
from enum import StrEnum

from app.query_governance.providers.base import (
    CompiledQuery, ExecutionPermit, QueryRecovery, RecoveryState,
)
from app.services.governed_query_service import (
    QueryRecoveryRequired, RecoveredQueryFailed, recover_governed_query,
)
from app.services.query_governance_service import QueryGovernanceService


def _expected_job_id(query: CompiledQuery, reservation_id: int) -> str:
    # Mirrors the stable job naming contract of BigQueryReadProvider._job_id.
    # The reservation ID plus server-issued request token identify one job.
    return (
        f"dr_query_w{query.workspace_id}_r{reservation_id}_"
        f"{query.request_token[:32]}"
    )


class _VerifiedRecoveryProvider:
    """Only expose recover(), validating job identity before any settlement."""

    identity = "BIGQUERY"

    def __init__(self, provider):
        self._provider = provider

    def recover(self, query: CompiledQuery, permit: ExecutionPermit) -> QueryRecovery:
        # Fail before provider IO if the claimed permit differs.
        permit.authorize(query, provider=self.identity)
        observed = self._provider.recover(query, permit)
        expected_id = _expected_job_id(query, permit.reservation_id)
        if (
            type(observed) is not QueryRecovery
            or observed.provider_job_id != expected_id
            or type(observed.state) is not RecoveryState
            or (
                observed.state == RecoveryState.SUCCEEDED
                and (
                    observed.result is None
                    or observed.result.provider_job_id != expected_id
                )
            )
        ):
            raise ValueError("WAREHOUSE_RECOVERY_JOB_IDENTITY_MISMATCH")
        return observed


class WarehouseSettlementState(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WarehouseSettlement:
    reservation_id: int
    state: WarehouseSettlementState
    accounted_bytes: int | None
    execution_authorized: bool = False
    provider_dispatched: bool = False


def settle_claimed_warehouse_query(
    *,
    governance: QueryGovernanceService,
    query: CompiledQuery,
    reservation_id: int,
    provider,
) -> WarehouseSettlement:
    """Reconcile one claimed query using only provider job lookup.

    Missing/running/uncertain job observations raise QueryRecoveryRequired
    and keep reservation EXECUTING with its budget intact. A confirmed failed
    job uses durable conservative failure accounting; a confirmed successful
    job uses actual billing. No automatic retries or release.
    """
    if (
        type(governance) is not QueryGovernanceService
        or type(query) is not CompiledQuery
        or query.compiler_version != 2
        or query.workspace_id != governance.workspace_id
        or type(reservation_id) is not int or reservation_id < 1
        or getattr(provider, "identity", None) != "BIGQUERY"
    ):
        raise ValueError("Owned warehouse recovery settlement required")

    # The general-purpose recovery service checks owned EXECUTING reservation
    # identity, reconstructs a permit, calls provider.recover (not execute),
    # and atomically finalizes through QueryGovernanceService.complete/fail.
    try:
        result = recover_governed_query(
            governance=governance,provider=_VerifiedRecoveryProvider(provider),query=query,
            consumer="SQL",reservation_id=reservation_id,
        )
    except RecoveredQueryFailed:
        # A confirmed provider failure was already durably finalized by the
        # existing transaction. Inspect resulting ownership/accounting only.
        row=governance.reservation(reservation_id)
        if row.get("status") != "FAILED":
            raise QueryRecoveryRequired(reservation_id)
        return WarehouseSettlement(
            reservation_id=reservation_id,
            state=WarehouseSettlementState.FAILED,
            accounted_bytes=None,
        )
    if result.usage is None:
        raise QueryRecoveryRequired(reservation_id)
    return WarehouseSettlement(
        reservation_id=reservation_id,
        state=WarehouseSettlementState.SUCCEEDED,
        accounted_bytes=result.usage["accounted_bytes"],
    )
