"""11H-F1 central governed execution tests."""

import pytest

from app.query_governance.providers.base import (
    CompiledQuery,
    QueryExecutionResult,
)
from app.query_governance.types import (
    EstimateConfidence,
    QueryEstimate,
)
from app.services.governed_query_service import (
    GovernedExecutionState,
    QueryRecoveryRequired,
    execute_governed_query,
)


MIB = 1024 * 1024
TABLE = "synthetic-project.dr_dev_w1.m1_v1_g1"


def compiled():
    return CompiledQuery(
        workspace_id=1,
        request_token="a" * 64,
        intent_sha256="b" * 64,
        sql=f"SELECT id FROM `{TABLE}`",
        physical_tables=(TABLE,),
    )


def reservation(
    *,
    reservation_id=17,
    status="ACTIVE",
    reserved_bytes=50 * MIB,
):
    return {
        "reservation_id": reservation_id,
        "workspace_id": 1,
        "provider": "BIGQUERY",
        "request_token": "a" * 64,
        "intent_sha256": "b" * 64,
        "reserved_bytes": reserved_bytes,
        "maximum_bytes_billed": 256 * MIB,
        "output_rows": 500,
        "status": status,
    }


class Governance:
    workspace_id = 1

    def __init__(self, admitted=None):
        self.admitted = admitted
        self.calls = []
        self.complete_error = None

    def admit(self, **kwargs):
        self.calls.append(("admit", kwargs))
        return self.admitted

    def release(self, reservation_id):
        self.calls.append(("release", reservation_id))
        return {
            **reservation(
                reservation_id=reservation_id,
            ),
            "status": "RELEASED",
        }

    def claim(self, reservation_id):
        self.calls.append(("claim", reservation_id))
        return reservation(
            reservation_id=reservation_id,
            status="EXECUTING",
        )

    def complete(self, reservation_id, **kwargs):
        self.calls.append(
            ("complete", reservation_id, kwargs)
        )

        if self.complete_error is not None:
            raise self.complete_error

        return {
            "reservation_id": reservation_id,
            "outcome": "SUCCEEDED",
            **kwargs,
        }


class Provider:
    identity = "BIGQUERY"

    def __init__(self, estimates):
        self.estimates = list(estimates)
        self.calls = []
        self.execute_error = None

    def estimate(self, query):
        self.calls.append(("estimate", query))

        if not self.estimates:
            raise AssertionError("Unexpected estimate call")

        return self.estimates.pop(0)

    def execute(self, query, permit):
        self.calls.append(("execute", query, permit))

        if self.execute_error is not None:
            raise self.execute_error

        return QueryExecutionResult(
            provider_job_id="job-success",
            rows=({"id": "A"},),
            truncated=False,
            bytes_processed=20 * MIB,
            bytes_billed=25 * MIB,
            payload_bytes=10,
            duration_ms=250,
        )


def allowed_admission():
    return {
        "decision": "ALLOWED",
        "reservation": reservation(),
    }


def test_non_admitted_query_never_revalidates_or_executes():
    governance = Governance(
        admitted={
            "decision": "REVIEW_REQUIRED",
            "reservation": None,
        }
    )
    provider = Provider(
        [QueryEstimate(300 * MIB)]
    )

    outcome = execute_governed_query(
        governance=governance,
        provider=provider,
        query=compiled(),
        consumer="SQL",
    )

    assert outcome.state == GovernedExecutionState.NOT_ADMITTED
    assert len(provider.calls) == 1
    assert not any(
        call[0] in ("claim", "complete", "release")
        for call in governance.calls
    )


def test_preclaim_cost_drift_releases_active_reservation():
    governance = Governance(
        admitted=allowed_admission()
    )
    provider = Provider(
        [
            QueryEstimate(50 * MIB),
            QueryEstimate(60 * MIB),
        ]
    )

    outcome = execute_governed_query(
        governance=governance,
        provider=provider,
        query=compiled(),
        consumer="SQL",
    )

    assert (
        outcome.state
        == GovernedExecutionState.REVALIDATION_REQUIRED
    )

    assert ("release", 17) in governance.calls
    assert not any(
        call[0] == "claim"
        for call in governance.calls
    )
    assert not any(
        call[0] == "execute"
        for call in provider.calls
    )


@pytest.mark.parametrize(
    "fresh",
    [
        QueryEstimate(
            None,
            EstimateConfidence.UNKNOWN,
        ),
        QueryEstimate(
            10 * MIB,
            EstimateConfidence.LOWER_BOUND,
        ),
    ],
)
def test_uncertain_preclaim_revalidation_releases(fresh):
    governance = Governance(
        admitted=allowed_admission()
    )
    provider = Provider(
        [
            QueryEstimate(50 * MIB),
            fresh,
        ]
    )

    outcome = execute_governed_query(
        governance=governance,
        provider=provider,
        query=compiled(),
        consumer="SQL",
    )

    assert (
        outcome.state
        == GovernedExecutionState.REVALIDATION_REQUIRED
    )
    assert ("release", 17) in governance.calls


def test_successful_execution_uses_single_claim_and_completion():
    governance = Governance(
        admitted=allowed_admission()
    )
    provider = Provider(
        [
            QueryEstimate(50 * MIB),
            QueryEstimate(40 * MIB),
        ]
    )

    outcome = execute_governed_query(
        governance=governance,
        provider=provider,
        query=compiled(),
        consumer="SQL",
    )

    assert outcome.state == GovernedExecutionState.SUCCEEDED
    assert outcome.result.provider_job_id == "job-success"

    assert (
        len([
            call
            for call in provider.calls
            if call[0] == "estimate"
        ])
        == 2
    )

    assert (
        len([
            call
            for call in provider.calls
            if call[0] == "execute"
        ])
        == 1
    )

    assert ("claim", 17) in governance.calls

    assert (
        len([
            call
            for call in governance.calls
            if call[0] == "complete"
        ])
        == 1
    )

    assert not any(
        call[0] == "release"
        for call in governance.calls
    )


def test_provider_error_after_claim_requires_recovery():
    governance = Governance(
        admitted=allowed_admission()
    )
    provider = Provider(
        [
            QueryEstimate(50 * MIB),
            QueryEstimate(40 * MIB),
        ]
    )
    provider.execute_error = RuntimeError(
        "synthetic provider failure"
    )

    with pytest.raises(
        QueryRecoveryRequired,
        match="QUERY_RECOVERY_REQUIRED",
    ) as exc:
        execute_governed_query(
            governance=governance,
            provider=provider,
            query=compiled(),
            consumer="SQL",
        )

    assert exc.value.reservation_id == 17
    assert ("claim", 17) in governance.calls

    assert not any(
        call[0] in ("release", "complete")
        for call in governance.calls
    )


def test_completion_failure_requires_recovery_not_reexecution():
    governance = Governance(
        admitted=allowed_admission()
    )
    governance.complete_error = RuntimeError(
        "synthetic database failure"
    )

    provider = Provider(
        [
            QueryEstimate(50 * MIB),
            QueryEstimate(40 * MIB),
        ]
    )

    with pytest.raises(
        QueryRecoveryRequired,
        match="QUERY_RECOVERY_REQUIRED",
    ):
        execute_governed_query(
            governance=governance,
            provider=provider,
            query=compiled(),
            consumer="SQL",
        )

    assert (
        len([
            call
            for call in provider.calls
            if call[0] == "execute"
        ])
        == 1
    )

    assert not any(
        call[0] == "release"
        for call in governance.calls
    )


def test_workspace_mismatch_fails_before_provider_lookup():
    governance = Governance(
        admitted=allowed_admission()
    )
    governance.workspace_id = 2

    provider = Provider(
        [QueryEstimate(1)]
    )

    with pytest.raises(
        ValueError,
        match="workspace mismatch",
    ):
        execute_governed_query(
            governance=governance,
            provider=provider,
            query=compiled(),
            consumer="SQL",
        )

    assert provider.calls == []
    assert governance.calls == []