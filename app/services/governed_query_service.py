"""Central governed analytical-query execution.

Authorization and compilation happen before this layer.

This service owns the synchronous lifecycle:

estimate -> durable admission -> ACTIVE revalidation ->
claim -> bounded provider execution -> durable completion

It never retries an EXECUTING query.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from app.query_governance.providers.base import (
    CompiledQuery,
    ExecutionPermit,
    QueryExecutionResult,
    ReadQueryProvider,
)
from app.query_governance.types import (
    EstimateConfidence,
    QueryEstimate,
)
from app.services.query_governance_service import (
    QueryGovernanceService,
)


class GovernedExecutionState(StrEnum):
    NOT_ADMITTED = "NOT_ADMITTED"
    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"
    SUCCEEDED = "SUCCEEDED"


class QueryRecoveryRequired(RuntimeError):
    """Execution may have reached the provider and must not be retried."""

    def __init__(self, reservation_id: int):
        self.reservation_id = reservation_id
        super().__init__("QUERY_RECOVERY_REQUIRED")


@dataclass(frozen=True)
class GovernedQueryResult:
    state: GovernedExecutionState
    admission: Mapping[str, object]
    reservation_id: int | None = None
    result: QueryExecutionResult | None = None
    usage: Mapping[str, object] | None = None

    def __post_init__(self):
        if self.state == GovernedExecutionState.SUCCEEDED:
            if (
                self.reservation_id is None
                or self.result is None
                or self.usage is None
            ):
                raise ValueError(
                    "Successful governed execution requires durable evidence"
                )

        elif self.state == GovernedExecutionState.NOT_ADMITTED:
            if (
                self.reservation_id is not None
                or self.result is not None
                or self.usage is not None
            ):
                raise ValueError(
                    "Non-admitted query cannot contain execution evidence"
                )

        elif self.state == GovernedExecutionState.REVALIDATION_REQUIRED:
            if (
                self.reservation_id is None
                or self.result is not None
                or self.usage is not None
            ):
                raise ValueError(
                    "Revalidation result must contain released reservation only"
                )


def _reservation_from_admission(
    admission: Mapping[str, object],
) -> Mapping[str, object] | None:
    if not isinstance(admission, Mapping):
        raise ValueError("Typed governance admission required")

    reservation = admission.get("reservation")

    if reservation is None:
        return None

    if not isinstance(reservation, Mapping):
        raise ValueError("Invalid durable reservation")

    return reservation


def _fresh_estimate_fits_reservation(
    estimate: QueryEstimate,
    reservation: Mapping[str, object],
) -> bool:
    if not isinstance(estimate, QueryEstimate):
        return False

    if estimate.confidence not in (
        EstimateConfidence.EXACT,
        EstimateConfidence.UPPER_BOUND,
    ):
        return False

    if estimate.bytes_processed is None:
        return False

    reserved = reservation.get("reserved_bytes")

    return (
        type(reserved) is int
        and reserved >= 0
        and estimate.bytes_processed <= reserved
    )


def execute_governed_query(
    *,
    governance: QueryGovernanceService,
    provider: ReadQueryProvider,
    query: CompiledQuery,
    consumer: str,
    requested_rows: int | None = None,
    lease_seconds: int = 120,
) -> GovernedQueryResult:
    if not isinstance(query, CompiledQuery):
        raise ValueError("Compiled query required")

    if query.workspace_id != governance.workspace_id:
        raise ValueError("Governance/query workspace mismatch")

    provider_identity = getattr(provider, "identity", None)

    if (
        not isinstance(provider_identity, str)
        or not provider_identity
        or provider_identity != provider_identity.upper()
    ):
        raise ValueError("Explicit provider identity required")

    initial_estimate = provider.estimate(query)

    admission = governance.admit(
        consumer=consumer,
        provider=provider_identity,
        request_token=query.request_token,
        intent_sha256=query.intent_sha256,
        estimate=initial_estimate,
        requested_rows=requested_rows,
        lease_seconds=lease_seconds,
    )

    reservation = _reservation_from_admission(admission)

    # REVIEW_REQUIRED and BLOCKED never create a reservation.
    if reservation is None:
        return GovernedQueryResult(
            state=GovernedExecutionState.NOT_ADMITTED,
            admission=admission,
        )

    reservation_id = reservation.get("reservation_id")

    if type(reservation_id) is not int or reservation_id < 1:
        raise ValueError("Positive durable reservation id required")

    # Revalidate while still ACTIVE. At this point release is safe because
    # no provider execution has been attempted.
    fresh_estimate = provider.estimate(query)

    if not _fresh_estimate_fits_reservation(
        fresh_estimate,
        reservation,
    ):
        governance.release(reservation_id)

        return GovernedQueryResult(
            state=GovernedExecutionState.REVALIDATION_REQUIRED,
            admission=admission,
            reservation_id=reservation_id,
        )

    claimed = governance.claim(reservation_id)

    permit = ExecutionPermit.from_claimed_reservation(
        claimed
    )

    try:
        result = provider.execute(
            query,
            permit,
        )
    except Exception:
        # Claim means an execution attempt may have reached the provider.
        # Never release, retry or silently convert it to a fresh execution.
        raise QueryRecoveryRequired(
            reservation_id
        ) from None

    try:
        usage = governance.complete(
            reservation_id,
            bytes_processed=result.bytes_processed,
            bytes_billed=result.bytes_billed,
            provider_job_id=result.provider_job_id,
            duration_ms=result.duration_ms,
        )
    except Exception:
        # Provider succeeded but durable finalization did not. The
        # deterministic provider job must be recovered, not executed again.
        raise QueryRecoveryRequired(
            reservation_id
        ) from None

    return GovernedQueryResult(
        state=GovernedExecutionState.SUCCEEDED,
        admission=admission,
        reservation_id=reservation_id,
        result=result,
        usage=usage,
    )