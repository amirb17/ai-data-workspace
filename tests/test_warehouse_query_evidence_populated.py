"""Real publication-head and serving-receipt joins in disposable PostgreSQL.

The provider fixture uses a local fake BigQuery client; no cloud credentials,
live resources, or production metadata are accessed.
"""
from datetime import timedelta

import pytest

from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_provider_postgres import fact_provider_pg, provider_pg
from tests.test_warehouse_serving_postgres import publication, row
from tests.test_warehouse_reliability import NOW
from app.services import warehouse_query_evidence_service as evidence


def inspect(f):
    return evidence.snapshot(f.pg["workspace"], f.pg["owner"], [f.model])


def verify_blocked(response, publication_current, receipt_current):
    assert response["eligible"] is False
    assert response["query_execution_enabled"] is False
    assert response["provider_verification"] == "NOT_PERFORMED"
    assert response["models"][0]["publication_current"] is publication_current
    assert response["models"][0]["serving_receipt_current"] is receipt_current
    assert response["models"][0]["serving_verified"] is False
    assert "SERVING_NOT_VERIFIED" in response["blocked_reasons"]
    assert "WAREHOUSE_EXECUTION_DISABLED" in response["blocked_reasons"]


@pytest.mark.parametrize("fixture_name", ["fact_provider_pg", "provider_pg"])
def test_real_publication_head_and_materialization_receipt(fixture_name, request):
    f=request.getfixturevalue(fixture_name)
    initial=inspect(f)
    verify_blocked(initial, False, False)
    assert "PUBLICATION_NOT_CURRENT" in initial["blocked_reasons"]

    publication(f,[row("A")])
    published=inspect(f)
    verify_blocked(published, True, False)
    assert "PUBLICATION_NOT_CURRENT" not in published["blocked_reasons"]

    serving=f.provider.materialize_current_publication(f.resource)
    assert serving["publication_id"] > 0
    materialized=inspect(f)
    verify_blocked(materialized, True, True)
    assert "PUBLICATION_NOT_CURRENT" not in materialized["blocked_reasons"]
    # The materialization is a PostgreSQL receipt, never a live provider
    # health attestation or authorization to run SQL.


def test_stale_serving_receipt_is_not_current_after_publication_advances(fact_provider_pg):
    f=fact_provider_pg
    publication(f,[row("A")])
    first=f.provider.materialize_current_publication(f.resource)
    verify_blocked(inspect(f),True,True)

    later=NOW+timedelta(days=180)
    publication(f,[row("B",at=later)],key="second",at=later)
    changed=inspect(f)
    verify_blocked(changed,True,False)
    assert changed["models"][0]["publication_current"] is True
    assert changed["models"][0]["serving_receipt_current"] is False

    second=f.provider.materialize_current_publication(f.resource)
    assert second["publication_revision"] > first["publication_revision"]
    verify_blocked(inspect(f),True,True)


def test_foreign_owner_never_reads_publication_receipts(fact_provider_pg):
    from fastapi import HTTPException
    f=fact_provider_pg
    publication(f,[row("A")])
    f.provider.materialize_current_publication(f.resource)
    with pytest.raises(HTTPException) as caught:
        evidence.snapshot(f.pg["workspace"],f.pg["other"],[f.model])
    assert caught.value.status_code==403
