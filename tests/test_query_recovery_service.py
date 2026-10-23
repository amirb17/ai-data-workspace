"""11H-E3 recovery coordinator tests."""

import pytest

from app.query_governance.providers.base import (
    CompiledQuery,
    ExecutionPermit,
    QueryExecutionResult,
    QueryRecovery,
    RecoveryState,
)
from app.services.query_recovery_service import (
    reconcile_query_recovery,
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


def permit():
    return ExecutionPermit(
        reservation_id=17,
        workspace_id=1,
        provider="BIGQUERY",
        request_token="a" * 64,
        intent_sha256="b" * 64,
        reserved_bytes=50 * MIB,
        maximum_bytes_billed=256 * MIB,
        output_rows=500,
    )


class Governance:
    def __init__(self):
        self.complete_calls = []
        self.fail_calls = []

    def complete(self, reservation_id, **kwargs):
        self.complete_calls.append(
            (reservation_id, kwargs)
        )
        return {
            "reservation_id": reservation_id,
            "outcome": "SUCCEEDED",
            **kwargs,
        }

    def fail(self, reservation_id, **kwargs):
        self.fail_calls.append(
            (reservation_id, kwargs)
        )
        return {
            "reservation_id": reservation_id,
            "outcome": "FAILED",
            **kwargs,
        }


class Provider:
    def __init__(self, observation):
        self.observation = observation
        self.calls = 0

    def recover(self, query, execution_permit):
        self.calls += 1
        return self.observation


def test_successful_recovery_completes_durable_reservation():
    result = QueryExecutionResult(
        provider_job_id="job-success",
        rows=({"id": "A"},),
        truncated=False,
        bytes_processed=20 * MIB,
        bytes_billed=25 * MIB,
        payload_bytes=10,
        duration_ms=350,
    )

    provider = Provider(
        QueryRecovery(
            state=RecoveryState.SUCCEEDED,
            provider_job_id="job-success",
            result=result,
            bytes_processed=20 * MIB,
            bytes_billed=25 * MIB,
            duration_ms=350,
        )
    )
    governance = Governance()

    outcome = reconcile_query_recovery(
        governance=governance,
        provider=provider,
        query=compiled(),
        permit=permit(),
    )

    assert provider.calls == 1
    assert outcome.state == RecoveryState.SUCCEEDED
    assert outcome.result == result

    assert governance.complete_calls == [
        (
            17,
            {
                "bytes_processed": 20 * MIB,
                "bytes_billed": 25 * MIB,
                "provider_job_id": "job-success",
                "duration_ms": 350,
            },
        )
    ]

    assert governance.fail_calls == []


def test_failed_recovery_finalizes_failure_without_retry():
    provider = Provider(
        QueryRecovery(
            state=RecoveryState.FAILED,
            provider_job_id="job-failed",
            bytes_processed=None,
            bytes_billed=None,
            duration_ms=100,
        )
    )
    governance = Governance()

    outcome = reconcile_query_recovery(
        governance=governance,
        provider=provider,
        query=compiled(),
        permit=permit(),
    )

    assert provider.calls == 1
    assert outcome.state == RecoveryState.FAILED
    assert outcome.result is None

    assert governance.fail_calls == [
        (
            17,
            {
                "bytes_processed": None,
                "bytes_billed": None,
                "provider_job_id": "job-failed",
                "duration_ms": 100,
            },
        )
    ]

    assert governance.complete_calls == []


def test_in_progress_recovery_does_not_mutate_governance():
    provider = Provider(
        QueryRecovery(
            state=RecoveryState.IN_PROGRESS,
            provider_job_id="job-running",
        )
    )
    governance = Governance()

    outcome = reconcile_query_recovery(
        governance=governance,
        provider=provider,
        query=compiled(),
        permit=permit(),
    )

    assert outcome.state == RecoveryState.IN_PROGRESS
    assert governance.complete_calls == []
    assert governance.fail_calls == []


def test_not_found_recovery_does_not_release_or_finalize():
    provider = Provider(
        QueryRecovery(
            state=RecoveryState.NOT_FOUND,
            provider_job_id="job-missing",
        )
    )
    governance = Governance()

    outcome = reconcile_query_recovery(
        governance=governance,
        provider=provider,
        query=compiled(),
        permit=permit(),
    )

    assert outcome.state == RecoveryState.NOT_FOUND
    assert governance.complete_calls == []
    assert governance.fail_calls == []


def test_provider_recovery_error_never_mutates_governance():
    class BrokenProvider:
        def recover(self, query, execution_permit):
            raise RuntimeError("synthetic recovery failure")

    governance = Governance()

    with pytest.raises(
        RuntimeError,
        match="synthetic recovery failure",
    ):
        reconcile_query_recovery(
            governance=governance,
            provider=BrokenProvider(),
            query=compiled(),
            permit=permit(),
        )

    assert governance.complete_calls == []
    assert governance.fail_calls == []