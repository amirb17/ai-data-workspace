"""Regression for the first APPEND activation after verified Silver publication.

No AWS or DB writes; the production worker and SQL authority perform the
live lease, immutable-head, and empty-dataset checks.
"""
from copy import deepcopy

from app.processing_worker.integrated_executor import reusable_initial_append


def silver():
    return {
        "version": 1, "stage": "SILVER", "file_id": 2,
        "dataset_id": 1, "upload_id": 3, "association": 1,
        "rule_version": 1, "dataset_version": 1,
        "rules": [{"column_name": "employee_id", "rule_type": "NOT_NULL",
                   "rule_config": {"required": True}}],
        "upstream_receipt": "fenced-bronze-receipt",
        "upstream_manifest": {"key": "versioned-bronze"},
        "upstream_artifacts": {"data": {"version": "v1"}},
        "raw_source": {"key": "versioned-source"},
        "load_policy": None, "snapshot_context": None,
    }


def first_append(old):
    new = deepcopy(old)
    new["load_policy"] = {
        "policy_id": 1, "policy_version": 1,
        "dataset_id": 1, "dataset_version_id": 1,
        "load_strategy": "APPEND", "business_keys": [],
        "schema_evolution_policy": "STRICT", "event_time_column": None,
    }
    return new


def test_first_append_accepts_exact_immutable_silver_source():
    source = silver()
    assert reusable_initial_append(source, first_append(source))


def test_no_reuse_without_first_append_policy():
    source = silver()
    for strategy in ("UPSERT", "SNAPSHOT"):
        target = first_append(source)
        target["load_policy"]["load_strategy"] = strategy
        assert not reusable_initial_append(source, target)
    assert not reusable_initial_append(source, source)


def test_never_rebind_published_silver_from_a_different_source_or_rules():
    source = silver()
    for key, changed in (
        ("file_id", 4), ("dataset_id", 7), ("upload_id", 8),
        ("association", 9), ("rule_version", 2),
        ("dataset_version", 3), ("upstream_receipt", "other"),
        ("raw_source", {"key": "other"}),
        ("upstream_artifacts", {"data": {"version": "other"}}),
        ("rules", []),
    ):
        target = first_append(source)
        target[key] = changed
        assert not reusable_initial_append(source, target), key


def test_initial_append_policy_must_be_first_strict_and_keyless():
    source = silver()
    for key, value in (
        ("policy_version", 2),
        ("schema_evolution_policy", "ALLOW_ADDITIVE"),
        ("business_keys", ["employee_id"]),
        ("dataset_id", 20),
        ("dataset_version_id", 40),
        ("event_time_column", "created_at"),
    ):
        target = first_append(source)
        target["load_policy"][key] = value
        assert not reusable_initial_append(source, target), key


def test_does_not_rebind_policy_already_present_in_source():
    source = first_append(silver())
    target = first_append(source)
    assert not reusable_initial_append(source, target)


def test_requires_non_snapshot_no_other_contract_changes():
    source = silver()
    target = first_append(source)
    target["snapshot_context"] = {"coverage": "PARTIAL"}
    assert not reusable_initial_append(source, target)
    target = first_append(source)
    target["unexpected_permission"] = True
    assert not reusable_initial_append(source, target)
