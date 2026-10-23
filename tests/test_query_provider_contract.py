"""11H-D provider-neutral analytical read contract."""

from dataclasses import replace

import pytest

from app.query_governance.providers.base import (
    MAX_RESULT_PAYLOAD_BYTES,
    CompiledQuery,
    ExecutionPermit,
    QueryExecutionResult,
    QueryRecovery,
    ReadQueryProvider,
    RecoveryState,
)
from app.query_governance.types import QueryEstimate


TABLE = (
    "synthetic-project."
    "dr_dev_w1."
    "m1_v1_g1"
)


def query(**updates):
    value = dict(
        workspace_id=1,
        request_token="a" * 64,
        intent_sha256="b" * 64,
        sql=(
            "SELECT id FROM "
            "`synthetic-project.dr_dev_w1.m1_v1_g1`"
        ),
        physical_tables=(TABLE,),
    )
    value.update(updates)
    return CompiledQuery(**value)


def claimed(**updates):
    value = dict(
        reservation_id=1,
        workspace_id=1,
        provider="BIGQUERY",
        request_token="a" * 64,
        intent_sha256="b" * 64,
        reserved_bytes=100 * 1024 * 1024,
        maximum_bytes_billed=256 * 1024 * 1024,
        output_rows=500,
        status="EXECUTING",
    )
    value.update(updates)
    return value


def test_compiled_query_has_explicit_backend_physical_scope():
    compiled = query()

    assert compiled.compiler_version == 1
    assert compiled.physical_tables == (TABLE,)
    assert compiled.workspace_id == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"workspace_id": 0},
        {"request_token": "bad"},
        {"intent_sha256": "bad"},
        {"sql": ""},
        {"sql": "\x00SELECT 1"},
        {"compiler_version": 2},
    ],
)
def test_invalid_compiled_query_identity_is_rejected(updates):
    with pytest.raises(ValueError):
        query(**updates)


@pytest.mark.parametrize(
    "tables",
    [
        (),
        (TABLE, TABLE),
        ("other-project.bad.table",),
        ("synthetic-project.external-table.bad-name",),
        tuple(
            f"synthetic-project.dr_dev_w1.m{i}"
            for i in range(33)
        ),
    ],
)
def test_physical_table_scope_is_bounded_and_generated(tables):
    with pytest.raises(ValueError):
        query(physical_tables=tables)


def test_execution_permit_only_comes_from_claimed_reservation():
    permit = ExecutionPermit.from_claimed_reservation(claimed())

    assert permit.provider == "BIGQUERY"
    assert permit.maximum_bytes_billed == 256 * 1024 * 1024
    assert permit.output_rows == 500


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "ACTIVE"},
        {"status": "COMPLETED"},
        {"maximum_bytes_billed": (1 << 30) + 1},
        {"output_rows": 1001},
        {"provider": "bigquery"},
    ],
)
def test_execution_permit_rejects_invalid_or_unclaimed_state(updates):
    with pytest.raises(ValueError):
        ExecutionPermit.from_claimed_reservation(
            claimed(**updates)
        )


def test_execution_permit_requires_exact_query_scope():
    compiled = query()
    permit = ExecutionPermit.from_claimed_reservation(claimed())

    permit.authorize(compiled, provider="BIGQUERY")

    mismatches = (
    replace(
        compiled,
        workspace_id=2,
        physical_tables=(
            "synthetic-project.dr_dev_w2.m1_v1_g1",
        ),
        sql=(
            "SELECT id FROM "
            "`synthetic-project.dr_dev_w2.m1_v1_g1`"
        ),
    ),
    replace(compiled, request_token="c" * 64),
    replace(compiled, intent_sha256="d" * 64),
)

    for other in mismatches:
        with pytest.raises(ValueError, match="scope mismatch"):
            permit.authorize(other, provider="BIGQUERY")

    with pytest.raises(ValueError, match="scope mismatch"):
        permit.authorize(compiled, provider="DUCKDB")


def test_execution_result_is_bounded_and_carries_usage_evidence():
    result = QueryExecutionResult(
        provider_job_id="synthetic-job",
        rows=(
            {"id": "A"},
            {"id": "B"},
        ),
        truncated=False,
        bytes_processed=100,
        bytes_billed=100,
        payload_bytes=20,
        duration_ms=12,
    )

    assert result.row_count == 2
    assert result.bytes_processed == result.bytes_billed == 100


def test_execution_result_rejects_unbounded_payload():
    with pytest.raises(ValueError):
        QueryExecutionResult(
            provider_job_id="synthetic-job",
            rows=(),
            truncated=True,
            bytes_processed=1,
            bytes_billed=1,
            payload_bytes=MAX_RESULT_PAYLOAD_BYTES + 1,
            duration_ms=1,
        )


def test_provider_protocol_is_structural_and_provider_neutral():
    class SyntheticProvider:
        identity = "SYNTHETIC"
        def estimate(self, compiled):
            return QueryEstimate(1)

        def execute(self, compiled, permit):
            return QueryExecutionResult(
                provider_job_id="synthetic",
                rows=(),
                truncated=False,
                bytes_processed=1,
                bytes_billed=1,
                payload_bytes=0,
                duration_ms=1,
            )
        def recover(self, compiled, permit):
            return QueryRecovery(
                state=RecoveryState.NOT_FOUND,
                provider_job_id="synthetic",
            )
    assert isinstance(SyntheticProvider(), ReadQueryProvider)