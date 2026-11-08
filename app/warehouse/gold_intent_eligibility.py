
"""Fail-closed dataset Gold eligibility checks for Sydney warehouse intents.

Pure validation only. The caller must obtain records from an
authorized, consistent PostgreSQL transaction.

Does not create an intent, authorize a load, or call a cloud provider.
"""

from dataclasses import dataclass

from app.warehouse.accepted_changes import digest


class GoldEligibilityRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GoldEligibility:
    verification_id: int
    gold_run_id: int
    workspace_id: int
    dataset_id: int
    source_state_id: int
    source_fingerprint: str
    manifest_sha256: str
    catalog_sha256: str


def validate_gold_eligibility(
    *,
    workspace_id,
    source,
    config,
    dataset,
    gold_run,
    evidence,
    source_contract,
):
    """Validate current Gold evidence against an owned warehouse source."""

    if type(workspace_id) is not int or workspace_id <= 0:
        raise GoldEligibilityRejected("Invalid workspace")

    if not all(
        isinstance(value, dict)
        for value in (
            source,
            config,
            dataset,
            gold_run,
            evidence,
            source_contract,
        )
    ):
        raise GoldEligibilityRejected("Complete trusted records required")

    dataset_id = source.get("source_dataset_id")

    if type(dataset_id) is not int or dataset_id <= 0:
        raise GoldEligibilityRejected("Invalid warehouse source dataset")

    if (
        dataset.get("dataset_id") != dataset_id
        or dataset.get("workspace_id") != workspace_id
        or dataset.get("status") != "ACTIVE"
    ):
        raise GoldEligibilityRejected("Warehouse source scope mismatch")

    if (
        dataset.get("state_analytics_status") != "FRESH"
        or dataset.get("current_state_id") is None
        or dataset.get("current_gold_run_id") is None
    ):
        raise GoldEligibilityRejected("Current fresh Gold required")

    if (
        gold_run.get("gold_run_id") != dataset["current_gold_run_id"]
        or gold_run.get("dataset_id") != dataset_id
        or gold_run.get("source_state_id") != dataset["current_state_id"]
        or gold_run.get("status") != "SUCCESS"
        or gold_run.get("build_version") != 1
    ):
        raise GoldEligibilityRejected("Current successful Gold run required")

    if (
        type(evidence.get("verification_id")) is not int
        or evidence["verification_id"] <= 0
        or evidence.get("gold_run_id") != gold_run["gold_run_id"]
        or evidence.get("contract_version") != 1
    ):
        raise GoldEligibilityRejected("Persisted Gold verification required")

    if (
        source_contract.get("version") != 1
        or source_contract.get("workspace_id") != workspace_id
        or source_contract.get("dataset_id") != dataset_id
        or source_contract.get("state_id") != dataset["current_state_id"]
        or evidence.get("source_contract") != source_contract
        or evidence.get("source_fingerprint") != digest(source_contract)
    ):
        raise GoldEligibilityRejected("Gold source contract drift")

    history = source_contract.get("history")

    if not isinstance(history, list) or not 1 <= len(history) <= 64:
        raise GoldEligibilityRejected("Bounded accepted history required")

    # source_snapshot() stores the history in chronological order
    # inside the contract, so the last entry is the current state.
    latest = history[-1]

    if not isinstance(latest, dict):
        raise GoldEligibilityRejected("Invalid current source history")

    state = latest.get("state")
    application = latest.get("application")

    if not isinstance(state, dict) or not isinstance(application, dict):
        raise GoldEligibilityRejected("Current source pins required")


    if (
        state.get("state_id") != dataset["current_state_id"]
        or state.get("dataset_version_id")
            != config.get("source_dataset_version_id")
        or application.get("policy_id")
            != config.get("source_policy_id")
        or application.get("applied_rule_version")
            != config.get("source_rule_version")
        or gold_run.get("dataset_version_id")
            != state.get("dataset_version_id")
        or gold_run.get("policy_id")
            != application.get("policy_id")
        or gold_run.get("source_sha256")
            != state.get("manifest_sha256")
    ):
        raise GoldEligibilityRejected(
            "Warehouse source contract mismatch"
        )


    if (
        evidence.get("manifest_key") != gold_run.get("manifest_key")
        or evidence.get("manifest_sha256")
            != gold_run.get("manifest_sha256")
        or evidence.get("catalog_sha256")
            != digest(gold_run.get("catalog"))
    ):
        raise GoldEligibilityRejected("Gold artifact evidence mismatch")

    return GoldEligibility(
        verification_id=evidence["verification_id"],
        gold_run_id=gold_run["gold_run_id"],
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        source_state_id=dataset["current_state_id"],
        source_fingerprint=evidence["source_fingerprint"],
        manifest_sha256=evidence["manifest_sha256"],
        catalog_sha256=evidence["catalog_sha256"],
    )
