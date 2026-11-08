
"""PostgreSQL integration tests for Sydney Gold resolution."""

import pytest

from app.services.sydney_gold_eligibility_service import (
    resolve_current_gold_for_warehouse,
)
from app.warehouse.gold_intent_eligibility import (
    GoldEligibilityRejected,
)
from tests.test_task21h158e1_gold_postgres import (
    pg,
    source,
    pipeline,
    accepted,
    refresh,
)
from tests.test_task21h158e1_warehouse_postgres import setup


def test_missing_independent_gold_verification_rejected(
    accepted,
    monkeypatch,
):
    f = accepted("UPSERT")
    provider, _, _ = setup(f, monkeypatch)

    with pytest.raises(GoldEligibilityRejected):
        resolve_current_gold_for_warehouse(
            f.workspace,
            provider.reliability.scope[1],
            provider.reliability.scope[2],
            f.user,
        )


def test_gold_dependency_epoch_drift_rejected(
    accepted,
    monkeypatch,
):
    f = accepted("UPSERT")
    provider, _, _ = setup(f, monkeypatch)

    refresh(f)

    with f.src[0]() as conn:
        conn.execute(
            """
            INSERT INTO processing_dependency_epochs (
                dataset_id, revision
            )
            VALUES (%s, 1)
            ON CONFLICT (dataset_id)
            DO UPDATE SET revision =
                processing_dependency_epochs.revision + 1
            """,
            (f.dataset,),
        )

    with pytest.raises(GoldEligibilityRejected):
        resolve_current_gold_for_warehouse(
            f.workspace,
            provider.reliability.scope[1],
            provider.reliability.scope[2],
            f.user,
        )
