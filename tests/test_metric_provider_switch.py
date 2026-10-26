"""A1.6 controlled Metric provider switch and rollback contracts."""

from contextlib import nullcontext
from copy import deepcopy

import pytest

from app.processing.metric_execution import PandasMetricExecutionEngine
from app.processing.metric_shadow import ShadowOutcome
from app.services import metric_rollout_service as rollout
from app.services.metric_rollout_service import MetricProviderSelection
from app.services.metric_shadow_service import (
    MetricShadowEvidence,
    MetricShadowExecution,
)
from tests.metric_shadow_fixture import USER, frame, plan


def metric_case():
    execution_plan = plan()
    frames = {10: frame([1, 2])}
    pandas_result = PandasMetricExecutionEngine().execute(
        execution_plan,
        frames,
    )
    return execution_plan, frames, pandas_result


def fake_shadow(monkeypatch, execution):
    class FakeMetricShadowService:
        def __init__(self, *args, **kwargs):
            pass

        def compare(self, *args, **kwargs):
            return execution

    monkeypatch.setattr(
        rollout,
        "MetricShadowService",
        FakeMetricShadowService,
    )


def test_primary_defaults_off_without_touching_shadow(monkeypatch):
    monkeypatch.delenv(
        "METRIC_BIGQUERY_PRIMARY_ENABLED",
        raising=False,
    )
    monkeypatch.setenv(
        "METRIC_BIGQUERY_SHADOW_ENABLED",
        "true",
    )

    execution_plan, frames, pandas_result = metric_case()

    def forbidden(*args, **kwargs):
        pytest.fail("Primary-disabled Metric touched BigQuery shadow")

    monkeypatch.setattr(
        rollout,
        "MetricShadowService",
        forbidden,
    )

    selected = rollout.select_metric_output(
        execution_plan,
        pandas_result,
        USER,
        frames=frames,
    )

    assert selected.provider == "PANDAS"
    assert selected.reason == "PRIMARY_DISABLED"
    assert selected.output == pandas_result
    assert selected.evidence is None


def test_primary_requires_shadow_rollout(monkeypatch):
    monkeypatch.setenv(
        "METRIC_BIGQUERY_PRIMARY_ENABLED",
        "true",
    )
    monkeypatch.delenv(
        "METRIC_BIGQUERY_SHADOW_ENABLED",
        raising=False,
    )

    execution_plan, frames, pandas_result = metric_case()

    def forbidden(*args, **kwargs):
        pytest.fail("Shadow-disabled primary rollout constructed provider")

    monkeypatch.setattr(
        rollout,
        "MetricShadowService",
        forbidden,
    )

    selected = rollout.select_metric_output(
        execution_plan,
        pandas_result,
        USER,
        frames=frames,
    )

    assert selected.provider == "PANDAS"
    assert selected.reason == "SHADOW_DISABLED"
    assert selected.output == pandas_result


def test_exact_match_promotes_bigquery(monkeypatch):
    monkeypatch.setenv(
        "METRIC_BIGQUERY_PRIMARY_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "METRIC_BIGQUERY_SHADOW_ENABLED",
        "true",
    )

    execution_plan, frames, pandas_result = metric_case()

    evidence = MetricShadowEvidence(
        ShadowOutcome.MATCH,
        execution_plan.signature,
    )

    warehouse_result = deepcopy(pandas_result)

    fake_shadow(
        monkeypatch,
        MetricShadowExecution(
            pandas_result,
            evidence,
            warehouse_result,
        ),
    )

    selected = rollout.select_metric_output(
        execution_plan,
        pandas_result,
        USER,
        frames=frames,
    )

    assert selected.provider == "BIGQUERY"
    assert selected.reason == "MATCH"
    assert selected.output == pandas_result
    assert selected.evidence == evidence


def test_semantic_match_cannot_change_public_api(monkeypatch):
    monkeypatch.setenv(
        "METRIC_BIGQUERY_PRIMARY_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "METRIC_BIGQUERY_SHADOW_ENABLED",
        "true",
    )

    execution_plan, frames, pandas_result = metric_case()

    evidence = MetricShadowEvidence(
        ShadowOutcome.MATCH,
        execution_plan.signature,
    )

    warehouse_result = deepcopy(pandas_result)
    warehouse_result["value"] = "3.0"

    fake_shadow(
        monkeypatch,
        MetricShadowExecution(
            pandas_result,
            evidence,
            warehouse_result,
        ),
    )

    selected = rollout.select_metric_output(
        execution_plan,
        pandas_result,
        USER,
        frames=frames,
    )

    assert selected.provider == "PANDAS"
    assert selected.reason == "API_CONTRACT_MISMATCH"
    assert selected.output == pandas_result


@pytest.mark.parametrize(
    "outcome",
    [
        ShadowOutcome.DISABLED,
        ShadowOutcome.MISMATCH,
        ShadowOutcome.INELIGIBLE,
        ShadowOutcome.NOT_ADMITTED,
        ShadowOutcome.REVALIDATION_REQUIRED,
        ShadowOutcome.RECOVERY_REQUIRED,
        ShadowOutcome.PROVIDER_ERROR,
    ],
)
def test_every_non_match_outcome_falls_back_to_pandas(
    monkeypatch,
    outcome,
):
    monkeypatch.setenv(
        "METRIC_BIGQUERY_PRIMARY_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "METRIC_BIGQUERY_SHADOW_ENABLED",
        "true",
    )

    execution_plan, frames, pandas_result = metric_case()

    evidence = MetricShadowEvidence(
        outcome,
        execution_plan.signature,
    )

    fake_shadow(
        monkeypatch,
        MetricShadowExecution(
            pandas_result,
            evidence,
            None,
        ),
    )

    selected = rollout.select_metric_output(
        execution_plan,
        pandas_result,
        USER,
        frames=frames,
    )

    assert selected.provider == "PANDAS"
    assert selected.reason == f"SHADOW_{outcome.value}"
    assert selected.output == pandas_result
    assert selected.evidence == evidence


def test_unexpected_rollout_error_falls_back_to_pandas(monkeypatch):
    monkeypatch.setenv(
        "METRIC_BIGQUERY_PRIMARY_ENABLED",
        "true",
    )
    monkeypatch.setenv(
        "METRIC_BIGQUERY_SHADOW_ENABLED",
        "true",
    )

    execution_plan, frames, pandas_result = metric_case()

    class Broken:
        def __init__(self, *args, **kwargs):
            pass

        def compare(self, *args, **kwargs):
            raise RuntimeError("private provider failure")

    monkeypatch.setattr(
        rollout,
        "MetricShadowService",
        Broken,
    )

    selected = rollout.select_metric_output(
        execution_plan,
        pandas_result,
        USER,
        frames=frames,
    )

    assert selected.provider == "PANDAS"
    assert selected.reason == "PRIMARY_SERVICE_ERROR"
    assert selected.output == pandas_result
    assert selected.evidence is None


@pytest.mark.parametrize(
    "mode",
    ["match", "unexpected-error"],
)
def test_refresh_primary_rollout_never_breaks_fresh_result(
    monkeypatch,
    mode,
):
    from app.services import workspace_analytics_service as analytics

    execution_plan, frames, pandas_result = metric_case()
    stored = {}
    public = object()
    observed = []
    called = []

    evidence = MetricShadowEvidence(
        ShadowOutcome.MATCH,
        execution_plan.signature,
    )

    selection = MetricProviderSelection(
        output=deepcopy(pandas_result),
        provider="BIGQUERY",
        reason="MATCH",
        evidence=evidence,
    )

    class Connection:
        result = None

        def cursor(self, **kwargs):
            return self

        def execute(self, sql, params=None):
            if sql.startswith("SELECT result_id"):
                self.result = None

            elif "RETURNING result_id" in sql:
                self.result = {"result_id": 1}

            elif "SET status='FRESH'" in sql:
                stored.update(
                    status="FRESH",
                    output=deepcopy(params[0].obj),
                )

            elif (
                "SET status='FAILED',failure_code" in sql
                and "failure_code='INTERRUPTED'" not in sql
            ):
                pytest.fail(
                    "Provider rollout changed valid Pandas attempt to FAILED"
                )

            return self

        def fetchone(self):
            return self.result

    conn = Connection()

    monkeypatch.setattr(
        analytics,
        "validate_workspace",
        lambda *args: None,
    )

    monkeypatch.setattr(
        analytics.repository,
        "execution_lock",
        lambda *args: nullcontext(),
    )

    monkeypatch.setattr(
        analytics,
        "repository_transaction",
        lambda: nullcontext(conn),
    )

    monkeypatch.setattr(
        analytics,
        "resolve",
        lambda *args: [
            {
                "plan": execution_plan,
                "row": {
                    "candidate_id": 7,
                    "run_id": 1,
                },
            }
        ],
    )

    monkeypatch.setattr(
        analytics,
        "load_sources",
        lambda *args: (),
    )

    monkeypatch.setattr(
        analytics.BoundedMetricArtifacts,
        "create",
        lambda: object(),
    )

    monkeypatch.setattr(
        analytics,
        "read_frames",
        lambda *args: frames,
    )

    monkeypatch.setattr(
        analytics,
        "read_analytics",
        lambda *args: public,
    )

    monkeypatch.setattr(
        analytics,
        "primary_enabled",
        lambda: True,
    )

    def choose(*args, **kwargs):
        called.append(True)

        if mode == "unexpected-error":
            raise RuntimeError("synthetic rollout error")

        return selection

    monkeypatch.setattr(
        analytics,
        "select_metric_output",
        choose,
    )

    result = analytics.refresh_analytics(
        1,
        USER,
        shadow_observer=observed.append,
    )

    assert result is public
    assert called == [True]
    assert stored["status"] == "FRESH"
    assert stored["output"] == pandas_result

    if mode == "match":
        assert observed == [evidence]
    else:
        assert observed == []