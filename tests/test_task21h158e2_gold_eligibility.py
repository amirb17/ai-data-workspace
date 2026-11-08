
"""Offline tests for Gold-to-warehouse eligibility."""

from copy import deepcopy

import pytest

from app.services.sydney_gold_eligibility_service import resolve_current_gold_for_warehouse
from app.warehouse.accepted_changes import digest
from app.warehouse.gold_intent_eligibility import (
    GoldEligibilityRejected,
    validate_gold_eligibility,
)
from tests.test_task21h158e1_gold_postgres import refresh
from tests.test_task21h158e1_warehouse_postgres import setup


def valid_records():
    workspace_id = 1
    dataset_id = 10
    state_id = 20

    source = {
        "source_dataset_id": dataset_id,
        "source_dataset_version_id": 11,
        "source_policy_id": 12,
        "source_rule_version": 1,
    }
    config = {
        "source_dataset_version_id": 11,
        "source_policy_id": 12,
        "source_rule_version": 1,
    }

    dataset = {
        "dataset_id": dataset_id,
        "workspace_id": workspace_id,
        "status": "ACTIVE",
        "state_analytics_status": "FRESH",
        "current_state_id": state_id,
        "current_gold_run_id": 30,
    }

    gold_run = {
        "gold_run_id": 30,
        "dataset_id": dataset_id,
        "source_state_id": state_id,
        "dataset_version_id": 11,
        "policy_id": 12,
        "source_sha256": "a" * 64,
        "build_version": 1,
        "status": "SUCCESS",
        "manifest_key": "gold/manifest.json",
        "manifest_sha256": "b" * 64,
        "catalog": {"parts": ["part-1"]},
    }

    source_contract = {
        "version": 1,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "state_id": state_id,
        "dependency_epoch": 1,
        "history": [
            {
                "state": {
                    "state_id": state_id,
                    "dataset_version_id": 11,
                    "manifest_sha256": "a" * 64,
                },
                "application": {
                    "policy_id": 12,
                    "applied_rule_version": 1,
                },
            }
        ],
    }

    evidence = {
        "verification_id": 40,
        "gold_run_id": 30,
        "contract_version": 1,
        "source_contract": source_contract,
        "source_fingerprint": digest(source_contract),
        "manifest_key": gold_run["manifest_key"],
        "manifest_sha256": gold_run["manifest_sha256"],
        "catalog_sha256": digest(gold_run["catalog"]),
    }

    return {
        "workspace_id": workspace_id,
        "source": source,
        "dataset": dataset,
        "gold_run": gold_run,
        "evidence": evidence,
        "source_contract": source_contract,
        "config": config,
    }


def test_current_verified_gold_is_eligible():
    records = valid_records()

    result = validate_gold_eligibility(**records)

    assert result.verification_id == 40
    assert result.gold_run_id == 30
    assert result.source_state_id == 20
    assert result.source_fingerprint == digest(
        records["source_contract"]
    )


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("dataset", "state_analytics_status", "STALE"),
        ("dataset", "current_gold_run_id", 31),
        ("dataset", "current_state_id", 21),
        ("gold_run", "status", "FAILED"),
        ("gold_run", "source_state_id", 21),
        ("gold_run", "dataset_id", 99),
        ("gold_run", "manifest_sha256", "f" * 64),
        ("config", "source_policy_id", 99),
        ("config", "source_rule_version", 2),
        ("evidence", "verification_id", 0),
        ("evidence", "source_fingerprint", "0" * 64),
        ("evidence", "catalog_sha256", "0" * 64),
        ("config", "source_dataset_version_id", 99),
    ],
)
def test_gold_eligibility_rejects_drift(
    section, field, value
):
    records = valid_records()
    records = deepcopy(records)
    records[section][field] = value

    with pytest.raises(GoldEligibilityRejected):
        validate_gold_eligibility(**records)


def test_source_contract_substitution_rejected():
    records = valid_records()
    records = deepcopy(records)

    records["source_contract"]["dependency_epoch"] = 99

    with pytest.raises(GoldEligibilityRejected):
        validate_gold_eligibility(**records)


def test_missing_verification_rejected():
    records = valid_records()
    records["evidence"] = None

    with pytest.raises(GoldEligibilityRejected):
        validate_gold_eligibility(**records)
