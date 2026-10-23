"""Deterministic analytical query admission. Pure logic only."""

from app.query_governance.policy import QueryPolicy
from app.query_governance.types import (
    AdmissionDecision,
    Decision,
    EstimateConfidence,
    QueryEstimate,
    UsageSnapshot,
)


def _result(
    decision,
    reason,
    estimate,
    policy,
    output_rows,
    *,
    reserved_bytes=0,
):
    return AdmissionDecision(
        decision=decision,
        reason=reason,
        estimated_bytes=estimate.bytes_processed,
        maximum_bytes_billed=(
        policy.auto_execute_bytes
        if decision == Decision.ALLOWED
        else policy.hard_bytes
    ),
        output_rows=output_rows,
        reserved_bytes=reserved_bytes,
    )


def decide(
    estimate: QueryEstimate,
    usage: UsageSnapshot,
    *,
    requested_rows: int | None = None,
    policy: QueryPolicy | None = None,
) -> AdmissionDecision:
    policy = policy or QueryPolicy()

    output_rows = (
        policy.default_output_rows
        if requested_rows is None
        else requested_rows
    )

    if type(output_rows) is not int or output_rows < 1:
        return _result(
            Decision.BLOCKED,
            "INVALID_OUTPUT_ROWS",
            estimate,
            policy,
            policy.default_output_rows,
        )

    if output_rows > policy.maximum_output_rows:
        return _result(
            Decision.BLOCKED,
            "OUTPUT_ROW_LIMIT_EXCEEDED",
            estimate,
            policy,
            policy.maximum_output_rows,
        )

    if (
        estimate.bytes_processed is None
        or estimate.confidence == EstimateConfidence.UNKNOWN
    ):
        return _result(
            Decision.BLOCKED,
            "ESTIMATE_UNKNOWN",
            estimate,
            policy,
            output_rows,
        )

    if estimate.confidence == EstimateConfidence.LOWER_BOUND:
        return _result(
            Decision.BLOCKED,
            "ESTIMATE_NOT_SAFE_UPPER_BOUND",
            estimate,
            policy,
            output_rows,
        )

    estimated = estimate.bytes_processed

    if estimated > policy.hard_bytes:
        return _result(
            Decision.BLOCKED,
            "HARD_SCAN_LIMIT_EXCEEDED",
            estimate,
            policy,
            output_rows,
        )

    if usage.workspace_active_queries >= policy.workspace_concurrency:
        return _result(
            Decision.BLOCKED,
            "WORKSPACE_CONCURRENCY_LIMIT",
            estimate,
            policy,
            output_rows,
        )

    if usage.global_active_queries >= policy.global_concurrency:
        return _result(
            Decision.BLOCKED,
            "GLOBAL_CONCURRENCY_LIMIT",
            estimate,
            policy,
            output_rows,
        )

    projected_daily = (
        usage.committed_bytes_today
        + usage.reserved_bytes_today
        + estimated
    )

    if projected_daily > policy.workspace_daily_bytes:
        return _result(
            Decision.BLOCKED,
            "WORKSPACE_DAILY_BUDGET_EXCEEDED",
            estimate,
            policy,
            output_rows,
        )
    projected_monthly = (
        usage.project_committed_bytes_month
        + usage.project_reserved_bytes_month
        + estimated
    )

    if projected_monthly > policy.project_monthly_bytes:
        return _result(
            Decision.BLOCKED,
            "PROJECT_MONTHLY_BUDGET_EXCEEDED",
            estimate,
            policy,
            output_rows,
        )

    if estimated > policy.auto_execute_bytes:
        return _result(
            Decision.REVIEW_REQUIRED,
            "ABOVE_AUTOMATIC_SCAN_LIMIT",
            estimate,
            policy,
            output_rows,
        )

    return _result(
        Decision.ALLOWED,
        "WITHIN_AUTOMATIC_POLICY",
        estimate,
        policy,
        output_rows,
        reserved_bytes=estimated,
    )
