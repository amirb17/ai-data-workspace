"""Controlled Metric provider rollout with deterministic Pandas fallback."""

from copy import deepcopy
from dataclasses import dataclass
import os

from app.processing.metric_shadow import ShadowOutcome
from app.services.metric_shadow_service import (
    MetricShadowEvidence,
    MetricShadowService,
    shadow_enabled,
)


PANDAS_PROVIDER = "PANDAS"
BIGQUERY_PROVIDER = "BIGQUERY"


def primary_enabled():
    """BigQuery Metric result promotion is explicit and off by default."""
    return (
        os.getenv("METRIC_BIGQUERY_PRIMARY_ENABLED", "false").lower()
        == "true"
    )


@dataclass(frozen=True)
class MetricProviderSelection:
    output: dict
    provider: str
    reason: str
    evidence: MetricShadowEvidence | None = None


def _pandas(pandas_result, reason, evidence=None):
    return MetricProviderSelection(
        output=deepcopy(pandas_result),
        provider=PANDAS_PROVIDER,
        reason=reason,
        evidence=evidence,
    )


def select_metric_output(
    plan,
    pandas_result,
    user,
    *,
    frames=None,
):
    """
    Select the user-visible Metric provider.

    BigQuery is promoted only when:
      1. primary rollout is explicitly enabled,
      2. shadow execution is also enabled,
      3. governed BigQuery execution returns MATCH,
      4. a warehouse result exists,
      5. its public output contract is exactly equal to Pandas.

    Every other condition falls back to Pandas.
    """
    if not primary_enabled():
        return _pandas(
            pandas_result,
            "PRIMARY_DISABLED",
        )

    # Primary promotion is deliberately built on the already-proven
    # A1.4/A1.5 shadow/governance path.
    if not shadow_enabled():
        return _pandas(
            pandas_result,
            "SHADOW_DISABLED",
        )

    try:
        execution = MetricShadowService(
            plan.workspace_id,
            user,
        ).compare(
            plan,
            pandas_result,
            frames=frames,
        )
    except Exception:
        # Provider-selection failures must never replace an otherwise-valid
        # authoritative Pandas Metric result.
        return _pandas(
            pandas_result,
            "PRIMARY_SERVICE_ERROR",
        )

    evidence = execution.evidence

    if evidence.outcome != ShadowOutcome.MATCH:
        return _pandas(
            pandas_result,
            f"SHADOW_{evidence.outcome.value}",
            evidence,
        )

    if execution.warehouse_result is None:
        return _pandas(
            pandas_result,
            "WAREHOUSE_RESULT_MISSING",
            evidence,
        )

    # A1.5 proves semantic equality. A1.6 additionally requires exact
    # public-result equality so enabling the provider cannot change the
    # existing API representation (for example Decimal string scale).
    if execution.warehouse_result != pandas_result:
        return _pandas(
            pandas_result,
            "API_CONTRACT_MISMATCH",
            evidence,
        )

    return MetricProviderSelection(
        output=deepcopy(execution.warehouse_result),
        provider=BIGQUERY_PROVIDER,
        reason="MATCH",
        evidence=evidence,
    )