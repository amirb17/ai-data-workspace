"""Coordinate provider recovery with durable query-governance state.

This layer never retries provider execution. It only finalizes a durable
EXECUTING reservation when provider recovery proves success or failure.
"""

from dataclasses import dataclass
from typing import Mapping

from app.query_governance.providers.base import (
    CompiledQuery,
    ExecutionPermit,
    QueryExecutionResult,
    QueryRecovery,
    ReadQueryProvider,
    RecoveryState,
)
from app.services.query_governance_service import (
    QueryGovernanceService,
)


@dataclass(frozen=True)
class RecoveryOutcome:
    state: RecoveryState
    provider_job_id: str
    result: QueryExecutionResult | None = None
    usage: Mapping[str, object] | None = None

    def __post_init__(self):
        if (
            not isinstance(self.provider_job_id, str)
            or not self.provider_job_id
        ):
            raise ValueError("Provider job identity required")

        if self.state == RecoveryState.SUCCEEDED:
            if self.result is None or self.usage is None:
                raise ValueError(
                    "Successful recovery requires result and usage"
                )

        elif self.state == RecoveryState.FAILED:
            if self.result is not None or self.usage is None:
                raise ValueError(
                    "Failed recovery requires durable usage only"
                )

        elif self.state in (
            RecoveryState.IN_PROGRESS,
            RecoveryState.NOT_FOUND,
        ):
            if self.result is not None or self.usage is not None:
                raise ValueError(
                    "Unresolved recovery cannot finalize durable state"
                )

        else:
            raise ValueError("Unsupported recovery state")


def reconcile_query_recovery(
    *,
    governance: QueryGovernanceService,
    provider: ReadQueryProvider,
    query: CompiledQuery,
    permit: ExecutionPermit,
) -> RecoveryOutcome:
    if not isinstance(query, CompiledQuery):
        raise ValueError("Compiled query required")

    if not isinstance(permit, ExecutionPermit):
        raise ValueError("Execution permit required")

    observation: QueryRecovery = provider.recover(
        query,
        permit,
    )

    if observation.state == RecoveryState.SUCCEEDED:
        result = observation.result

        if result is None:
            raise ValueError(
                "Successful recovery missing execution result"
            )

        usage = governance.complete(
            permit.reservation_id,
            bytes_processed=result.bytes_processed,
            bytes_billed=result.bytes_billed,
            provider_job_id=result.provider_job_id,
            duration_ms=result.duration_ms,
        )

        return RecoveryOutcome(
            state=RecoveryState.SUCCEEDED,
            provider_job_id=result.provider_job_id,
            result=result,
            usage=usage,
        )

    if observation.state == RecoveryState.FAILED:
        usage = governance.fail(
            permit.reservation_id,
            bytes_processed=observation.bytes_processed,
            bytes_billed=observation.bytes_billed,
            provider_job_id=observation.provider_job_id,
            duration_ms=observation.duration_ms,
        )

        return RecoveryOutcome(
            state=RecoveryState.FAILED,
            provider_job_id=observation.provider_job_id,
            usage=usage,
        )

    if observation.state in (
        RecoveryState.IN_PROGRESS,
        RecoveryState.NOT_FOUND,
    ):
        return RecoveryOutcome(
            state=observation.state,
            provider_job_id=observation.provider_job_id,
        )

    raise ValueError("Unsupported provider recovery state")