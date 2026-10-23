
"""11H service boundary over durable query-governance primitives."""

from pathlib import Path

import pytest

from app.db import query_governance_repository as repository
from app.query_governance.admission import decide
from app.query_governance.policy import MIB, QueryPolicy
from app.query_governance.types import Decision, QueryEstimate, UsageSnapshot
from app.services.query_governance_service import QueryGovernanceService
from tests.test_query_governance_postgres import governance_pg
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_foundation import pg



ROOT = Path(__file__).resolve().parents[1]


def service(fixture, **policy):
    return QueryGovernanceService(
        fixture["workspace"],
        fixture["owner"],
        policy=QueryPolicy(**policy),
    )


def test_service_admit_claim_complete_lifecycle(governance_pg):
    svc = service(governance_pg)

    admitted = svc.admit(
        consumer="SQL",
        provider="BIGQUERY",
        request_token="1" * 64,
        intent_sha256="2" * 64,
        estimate=QueryEstimate(100 * MIB),
    )

    assert admitted["decision"].decision == Decision.ALLOWED
    assert admitted["reservation"]["status"] == "ACTIVE"

    reservation_id = admitted["reservation"]["reservation_id"]

    claimed = svc.claim(reservation_id)

    assert claimed["status"] == "EXECUTING"

    usage = svc.complete(
        reservation_id,
        bytes_processed=120 * MIB,
        bytes_billed=130 * MIB,
        provider_job_id="service-test-job",
        duration_ms=321,
    )

    assert usage["accounted_bytes"] == 130 * MIB

    snapshot = svc.usage()

    assert snapshot.committed_bytes_today == 130 * MIB
    assert snapshot.reserved_bytes_today == 0
    assert snapshot.workspace_active_queries == 0


def test_service_release_without_execution(governance_pg):
    svc = service(governance_pg)

    admitted = svc.admit(
        consumer="ANALYTICS",
        provider="BIGQUERY",
        request_token="3" * 64,
        intent_sha256="4" * 64,
        estimate=QueryEstimate(10 * MIB),
    )

    reservation_id = admitted["reservation"]["reservation_id"]

    released = svc.release(reservation_id)

    assert released["status"] == "RELEASED"

    snapshot = svc.usage()

    assert snapshot.committed_bytes_today == 0
    assert snapshot.reserved_bytes_today == 0
    assert snapshot.workspace_active_queries == 0


def test_review_required_never_creates_reservation(governance_pg):
    svc = service(governance_pg)

    result = svc.admit(
        consumer="ASK",
        provider="BIGQUERY",
        request_token="5" * 64,
        intent_sha256="6" * 64,
        estimate=QueryEstimate(300 * MIB),
    )

    assert result["decision"].decision == Decision.REVIEW_REQUIRED
    assert result["reservation"] is None

    snapshot = svc.usage()

    assert snapshot.reserved_bytes_today == 0
    assert snapshot.workspace_active_queries == 0


def test_service_preserves_request_idempotency_contract(governance_pg):
    svc = service(governance_pg)

    first = svc.admit(
        consumer="METRIC",
        provider="BIGQUERY",
        request_token="7" * 64,
        intent_sha256="8" * 64,
        estimate=QueryEstimate(20 * MIB),
    )

    again = svc.admit(
        consumer="METRIC",
        provider="BIGQUERY",
        request_token="7" * 64,
        intent_sha256="8" * 64,
        estimate=QueryEstimate(20 * MIB),
    )

    assert again["reused"]
    assert (
        first["reservation"]["reservation_id"]
        == again["reservation"]["reservation_id"]
    )

    with pytest.raises(
        repository.ReservationConflict,
        match="REQUEST_TOKEN_CONFLICT",
    ):
        svc.admit(
            consumer="METRIC",
            provider="BIGQUERY",
            request_token="7" * 64,
            intent_sha256="8" * 64,
            estimate=QueryEstimate(21 * MIB),
        )


def test_service_cannot_cross_workspace_ownership(governance_pg):
    foreign = QueryGovernanceService(
        governance_pg["workspace"],
        governance_pg["other"],
        policy=QueryPolicy(),
    )

    with pytest.raises(
        repository.ReservationConflict,
        match="WORKSPACE_NOT_OWNED",
    ):
        foreign.admit(
            consumer="SQL",
            provider="BIGQUERY",
            request_token="a" * 64,
            intent_sha256="b" * 64,
            estimate=QueryEstimate(1),
        )


def test_service_is_execution_engine_and_consumer_neutral():
    service_source = (
        ROOT / "app/services/query_governance_service.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "google.cloud",
        "duckdb",
        "sqlglot",
        "pandas",
        "boto3",
        "gemini",
        "run_engine",
    )

    assert not any(value in service_source for value in forbidden)

    # 11H-core builds governance only. Consumer migration belongs to 11G.
    consumer_paths = (
        ROOT / "app/services/sql_analytics_service.py",
        ROOT / "app/services/workspace_analytics_service.py",
        ROOT / "app/services/workspace_question_service.py",
        ROOT / "app/services/metric_service.py",
    )

    for path in consumer_paths:
        assert "query_governance" not in path.read_text(
            encoding="utf-8"
        )
def test_allowed_query_execution_cap_is_automatic_limit():
    policy = QueryPolicy()

    result = decide(
        QueryEstimate(100 * MIB),
        UsageSnapshot(),
        policy=policy,
    )

    assert result.decision == Decision.ALLOWED
    assert result.maximum_bytes_billed == 256 * MIB
    assert result.maximum_bytes_billed == policy.auto_execute_bytes

def test_service_can_finalize_provider_confirmed_failure(governance_pg):
    svc = service(governance_pg)

    admitted = svc.admit(
        consumer="SQL",
        provider="BIGQUERY",
        request_token="c" * 64,
        intent_sha256="d" * 64,
        estimate=QueryEstimate(30 * MIB),
    )

    reservation_id = admitted["reservation"]["reservation_id"]

    svc.claim(reservation_id)

    usage = svc.fail(
        reservation_id,
        bytes_processed=None,
        bytes_billed=None,
        provider_job_id="confirmed-failed-job",
        duration_ms=25,
    )

    assert usage["outcome"] == "FAILED"
    assert usage["accounted_bytes"] == 30 * MIB
    assert svc.usage().workspace_active_queries == 0