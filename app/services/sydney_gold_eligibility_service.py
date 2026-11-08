
"""Read-only Sydney warehouse Gold eligibility assessment.

Uses existing owned warehouse scope and current dataset Gold authority.
Does not create warehouse intents or perform cloud operations.
"""

from app.services.warehouse_reliability_service import ReliabilityService
from app.services.dataset_gold_serving_service import (
    source_snapshot,
    row,
)
from app.warehouse.accepted_changes import digest
from app.warehouse.gold_intent_eligibility import (
    GoldEligibilityRejected,
    validate_gold_eligibility,
)


def resolve_current_gold_for_warehouse(
    workspace_id,
    model_id,
    version_id,
    user,
):
    """Resolve current verified Gold for the model's pinned source."""

    reliability = ReliabilityService(
        workspace_id,
        model_id,
        version_id,
        user,
    )

    # Reuse the authorized, approved warehouse-model transaction.
    with reliability.transaction() as (
        conn,
        source,
        config,
        settings,
    ):
        dataset_id = source["source_dataset_id"]

        # Reconstruct the current accepted source contract while
        # holding the existing source-scope locks.
        dataset, contract, _ = source_snapshot(
            conn,
            workspace_id,
            dataset_id,
            user,
            lock=True,
        )

        gold_run_id = dataset["current_gold_run_id"]

        if gold_run_id is None:
            raise GoldEligibilityRejected(
                "Current dataset Gold run required"
            )

        gold_run = row(
            conn,
            """
            SELECT *
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
              AND dataset_id = %s
            FOR SHARE
            """,
            (gold_run_id, dataset_id),
        )

        if gold_run is None:
            raise GoldEligibilityRejected(
                "Current registered Gold run unavailable"
            )

        evidence = row(
            conn,
            """
            SELECT *
            FROM dataset_gold_evidence
            WHERE gold_run_id = %s
              AND source_fingerprint = %s
            FOR SHARE
            """,
            (gold_run_id, digest(contract)),
        )

        if evidence is None:
            raise GoldEligibilityRejected(
                "Current independent Gold verification required"
            )

        # The pure validator must agree with the database's
        # current source state, model mapping and Gold evidence.
        return validate_gold_eligibility(
            workspace_id=workspace_id,
            source=dict(source),
            config=dict(config),
            dataset=dict(dataset),
            gold_run=dict(gold_run),
            evidence=dict(evidence),
            source_contract=contract,
        )

def resolve_gold_in_transaction(
    conn,
    workspace_id,
    source,
    config,
    user,
):
    """Resolve verified current Gold in the caller's transaction."""

    dataset_id = source["source_dataset_id"]

    dataset, contract, _ = source_snapshot(
        conn,
        workspace_id,
        dataset_id,
        user,
        lock=True,
    )

    gold_run_id = dataset["current_gold_run_id"]

    if gold_run_id is None:
        raise GoldEligibilityRejected(
            "Current dataset Gold run required"
        )

    gold_run = row(
        conn,
        """
        SELECT *
        FROM dataset_gold_runs
        WHERE gold_run_id = %s
          AND dataset_id = %s
        FOR SHARE
        """,
        (gold_run_id, dataset_id),
    )

    if gold_run is None:
        raise GoldEligibilityRejected(
            "Current registered Gold run unavailable"
        )

    evidence = row(
        conn,
        """
        SELECT *
        FROM dataset_gold_evidence
        WHERE gold_run_id = %s
          AND source_fingerprint = %s
        FOR SHARE
        """,
        (gold_run_id, digest(contract)),
    )

    if evidence is None:
        raise GoldEligibilityRejected(
            "Current independent Gold verification required"
        )

    return validate_gold_eligibility(
        workspace_id=workspace_id,
        source=dict(source),
        config=dict(config),
        dataset=dict(dataset),
        gold_run=dict(gold_run),
        evidence=dict(evidence),
        source_contract=contract,
    )
