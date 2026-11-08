
"""PostgreSQL-backed Gold eligibility tests for Sydney warehouse.

Uses disposable schemas and synthetic local evidence only.
No cloud activation or warehouse publication.
"""

import pytest
from app.services.dataset_gold_serving_service import source_snapshot, row
from tests.test_task21h158e1_gold_postgres import (
    pg,
    source,
    pipeline,
    accepted,
    refresh,
)

from tests.test_task21h158e1_warehouse_postgres import setup

from app.services.sydney_gold_eligibility_service import (
    resolve_current_gold_for_warehouse,
)

from app.warehouse.gold_intent_eligibility import (
    GoldEligibilityRejected,
)


def test_current_verified_gold_resolves_from_postgres(
    accepted,
    monkeypatch,
):
    # Build an accepted dataset using the existing E1 fixture.
    f = accepted("UPSERT")

    # Create an approved warehouse model and source mapping.
    provider, resource_id, cloud = setup(f, monkeypatch)

    # Independently verify and publish the current dataset Gold.
    gold_run_id = refresh(f)

    # Resolve evidence using the actual PostgreSQL records.
    result = resolve_current_gold_for_warehouse(
        f.workspace,
        provider.reliability.scope[1],
        provider.reliability.scope[2],
        f.user,
    )

    assert result.workspace_id == f.workspace
    assert result.dataset_id == f.dataset
    assert result.gold_run_id == gold_run_id
    assert result.verification_id > 0
    assert len(result.source_fingerprint) == 64
    assert len(result.manifest_sha256) == 64
    assert len(result.catalog_sha256) == 64


def test_stale_gold_cannot_authorize_warehouse(
    accepted,
    monkeypatch,
):
    f = accepted("UPSERT")

    provider, resource_id, cloud = setup(f, monkeypatch)

    # Initially current and independently verified.
    refresh(f)

    first = resolve_current_gold_for_warehouse(
        f.workspace,
        provider.reliability.scope[1],
        provider.reliability.scope[2],
        f.user,
    )

    assert first.verification_id > 0

    # Publish a new accepted dataset state without refreshing Gold.
    f.next(b"id,city,amount\n1,B,15\n3,C,30\n")

    # The old Gold verification must no longer be eligible.
    with pytest.raises(GoldEligibilityRejected):
        resolve_current_gold_for_warehouse(
            f.workspace,
            provider.reliability.scope[1],
            provider.reliability.scope[2],
            f.user,
        )

def test_diagnose_gold_warehouse_source_pins(
    accepted,
    monkeypatch,
):
    f = accepted("UPSERT")
    provider, _, _ = setup(f, monkeypatch)
    refresh(f)

    with provider.reliability.transaction() as (
        conn,
        source,
        config,
        settings,
    ):
        dataset, contract, _ = source_snapshot(
            conn,
            f.workspace,
            f.dataset,
            f.user,
            lock=True,
        )

        gold_run = row(
            conn,
            """
            SELECT *
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
            """,
            (dataset["current_gold_run_id"],),
        )

        latest = contract["history"][-1]
        state = latest["state"]
        application = latest["application"]


        comparisons = {
            "dataset_id": (
                f.dataset,
                source["source_dataset_id"],
            ),
            "dataset_version": (
                state["dataset_version_id"],
                config["source_dataset_version_id"],
            ),
            "policy_id": (
                application["policy_id"],
                config["source_policy_id"],
            ),
            "rule_version": (
                application["applied_rule_version"],
                config["source_rule_version"],
            ),
            "gold_dataset_version": (
                gold_run["dataset_version_id"],
                state["dataset_version_id"],
            ),
            "gold_policy_id": (
                gold_run["policy_id"],
                application["policy_id"],
            ),
            "gold_source_sha256": (
                gold_run["source_sha256"],
                state["manifest_sha256"],
            ),
        }


        for name, (actual, expected) in comparisons.items():
            print(
                f"{name}: "
                f"{'MATCH' if actual == expected else 'MISMATCH'} "
                f"(actual={actual!r}, expected={expected!r})"
            )

def test_missing_independent_gold_verification_rejected(
    accepted,
    monkeypatch,
):
    f = accepted("UPSERT")
    provider, _, _ = setup(f, monkeypatch)

    # Accepted dataset exists, but no Gold refresh occurred.
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

    # Publish verified Gold first.
    refresh(f)

    # Simulate a changed source dependency epoch
    # inside the disposable PostgreSQL schema.
    with f.src[0]() as conn:
        conn.execute(
            """
            INSERT INTO processing_dependency_epochs (
                dataset_id,
                revision
            )
            VALUES (%s, 1)
            ON CONFLICT (dataset_id)
            DO UPDATE SET revision =
                processing_dependency_epochs.revision + 1
            """,
            (f.dataset,),
        )

    # The old Gold evidence must no longer authorize eligibility.
    with pytest.raises(GoldEligibilityRejected):
        resolve_current_gold_for_warehouse(
            f.workspace,
            provider.reliability.scope[1],
            provider.reliability.scope[2],
            f.user,
        )
