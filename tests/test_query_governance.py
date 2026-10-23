import pytest

from app.query_governance.admission import decide
from app.query_governance.policy import GIB, MIB, QueryPolicy
from app.query_governance.types import (
    Decision,
    EstimateConfidence,
    QueryEstimate,
    UsageSnapshot,
)


def test_small_query_is_allowed_and_reserves_estimate():
    result = decide(
        QueryEstimate(100 * MIB),
        UsageSnapshot(),
    )

    assert result.decision == Decision.ALLOWED
    assert result.reason == "WITHIN_AUTOMATIC_POLICY"
    assert result.reserved_bytes == 100 * MIB
    assert result.maximum_bytes_billed ==  QueryPolicy().auto_execute_bytes
    assert result.output_rows == 500


def test_boundary_at_256_mib_is_allowed():
    result = decide(
        QueryEstimate(256 * MIB),
        UsageSnapshot(),
    )

    assert result.decision == Decision.ALLOWED


def test_above_automatic_limit_requires_review():
    result = decide(
        QueryEstimate(256 * MIB + 1),
        UsageSnapshot(),
    )

    assert result.decision == Decision.REVIEW_REQUIRED
    assert result.reason == "ABOVE_AUTOMATIC_SCAN_LIMIT"
    assert result.reserved_bytes == 0


def test_exactly_one_gib_requires_review_not_block():
    result = decide(
        QueryEstimate(GIB),
        UsageSnapshot(),
    )

    assert result.decision == Decision.REVIEW_REQUIRED


def test_above_one_gib_is_blocked():
    result = decide(
        QueryEstimate(GIB + 1),
        UsageSnapshot(),
    )

    assert result.decision == Decision.BLOCKED
    assert result.reason == "HARD_SCAN_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    "estimate",
    [
        QueryEstimate(None, EstimateConfidence.UNKNOWN),
        QueryEstimate(1, EstimateConfidence.UNKNOWN),
    ],
)
def test_unknown_estimate_is_blocked(estimate):
    result = decide(estimate, UsageSnapshot())

    assert result.decision == Decision.BLOCKED
    assert result.reason == "ESTIMATE_UNKNOWN"


def test_lower_bound_estimate_is_blocked():
    result = decide(
        QueryEstimate(10 * MIB, EstimateConfidence.LOWER_BOUND),
        UsageSnapshot(),
    )

    assert result.decision == Decision.BLOCKED
    assert result.reason == "ESTIMATE_NOT_SAFE_UPPER_BOUND"


def test_safe_upper_bound_can_be_admitted():
    result = decide(
        QueryEstimate(100 * MIB, EstimateConfidence.UPPER_BOUND),
        UsageSnapshot(),
    )

    assert result.decision == Decision.ALLOWED


def test_daily_budget_counts_committed_and_reserved_usage():
    policy = QueryPolicy()

    result = decide(
        QueryEstimate(100 * MIB),
        UsageSnapshot(
            committed_bytes_today=4 * GIB,
            reserved_bytes_today=950 * MIB,
        ),
        policy=policy,
    )

    assert result.decision == Decision.BLOCKED
    assert result.reason == "WORKSPACE_DAILY_BUDGET_EXCEEDED"


def test_workspace_concurrency_is_blocked():
    policy = QueryPolicy()

    result = decide(
        QueryEstimate(1),
        UsageSnapshot(
            workspace_active_queries=policy.workspace_concurrency,
        ),
        policy=policy,
    )

    assert result.decision == Decision.BLOCKED
    assert result.reason == "WORKSPACE_CONCURRENCY_LIMIT"


def test_global_concurrency_is_blocked():
    policy = QueryPolicy()

    result = decide(
        QueryEstimate(1),
        UsageSnapshot(
            global_active_queries=policy.global_concurrency,
        ),
        policy=policy,
    )

    assert result.decision == Decision.BLOCKED
    assert result.reason == "GLOBAL_CONCURRENCY_LIMIT"


def test_default_rows_are_500():
    result = decide(QueryEstimate(0), UsageSnapshot())

    assert result.output_rows == 500


def test_explicit_rows_up_to_1000_are_allowed():
    result = decide(
        QueryEstimate(0),
        UsageSnapshot(),
        requested_rows=1000,
    )

    assert result.decision == Decision.ALLOWED
    assert result.output_rows == 1000


def test_more_than_1000_rows_is_blocked():
    result = decide(
        QueryEstimate(0),
        UsageSnapshot(),
        requested_rows=1001,
    )

    assert result.decision == Decision.BLOCKED
    assert result.reason == "OUTPUT_ROW_LIMIT_EXCEEDED"


def test_negative_estimate_is_invalid():
    with pytest.raises(ValueError):
        QueryEstimate(-1)


def test_negative_usage_is_invalid():
    with pytest.raises(ValueError):
        UsageSnapshot(committed_bytes_today=-1)


def test_environment_cannot_raise_hard_limit_above_one_gib(monkeypatch):
    monkeypatch.setenv("QUERY_HARD_BYTES", str(GIB + 1))

    with pytest.raises(ValueError):
        QueryPolicy.from_environment()


def test_environment_cannot_raise_auto_limit_above_256_mib(monkeypatch):
    monkeypatch.setenv("QUERY_AUTO_EXECUTE_BYTES", str(256 * MIB + 1))

    with pytest.raises(ValueError):
        QueryPolicy.from_environment()


def test_project_monthly_budget_counts_committed_and_reserved_usage():
    policy = QueryPolicy()

    result = decide(
        QueryEstimate(200 * MIB),
        UsageSnapshot(
            project_committed_bytes_month=99 * GIB,
            project_reserved_bytes_month=900 * MIB,
        ),
        policy=policy,
    )

    assert result.decision == Decision.BLOCKED
    assert result.reason == "PROJECT_MONTHLY_BUDGET_EXCEEDED"


def test_environment_cannot_raise_project_budget_above_100_gib(monkeypatch):
    monkeypatch.setenv(
        "QUERY_PROJECT_MONTHLY_BYTES",
        str(100 * GIB + 1),
    )

    with pytest.raises(ValueError):
        QueryPolicy.from_environment()
