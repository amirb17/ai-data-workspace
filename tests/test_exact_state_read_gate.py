import hashlib
import json

import pytest

from app.processing.append_engine import LINEAGE
from app.storage.incremental_artifacts import IncrementalArtifacts

from tests.test_exact_state_manifest import (
    Store,
    RULES,
    COLUMNS,
    PINS,
    sample,
)


def setup_state():
    storage = Store()
    artifacts = IncrementalArtifacts(storage)

    frame = sample()

    application = {
        "application_id": 8,
        "dataset_id": 55,
        "dataset_version_id": 39,
        "policy_id": 5,
        "upload_request_id": 21,
        "applied_rule_version": 1,
        "valid_rows": 2,
    }

    for name, value in zip(LINEAGE, [21, 8, 5, 1]):
        frame[name] = value

    data_hash, descriptor = artifacts.put_exact_frame(
        "test/data.parquet",
        frame,
        RULES,
        COLUMNS,
        **PINS,
    )

    outcomes = {"rejected": []}
    outcomes_hash = artifacts.put(
        "test/outcomes.json",
        artifacts.json(outcomes),
    )

    manifest = {
        "manifest_version": 2,
        "application_id": 8,
        "dataset_id": 55,
        "dataset_version_id": 39,
        "policy_id": 5,
        "upload_request_id": 21,
        "load_strategy": "APPEND",
        "normalization_version": 1,
        "data_key": "test/data.parquet",
        "data_sha256": data_hash,
        "outcomes_key": "test/outcomes.json",
        "outcomes_sha256": outcomes_hash,
        "row_count": 2,
        "effective_schema": descriptor["effective_schema"],
        "exact_descriptor": descriptor,
        "counts": {
            "inserted_rows": 2,
            "duplicate_rows": 0,
            "incremental_rejected_rows": 0,
        },
    }

    manifest_hash = artifacts.put(
        "test/manifest.json",
        artifacts.json(manifest),
    )

    policy = {
        "load_strategy": "APPEND",
        "normalization_version": 1,
    }

    return artifacts, application, policy, descriptor, manifest_hash


def read_state(
    artifacts,
    application,
    policy,
    descriptor,
    manifest_hash,
    *,
    rules=RULES,
):
    return artifacts.validate_state(
        "test/manifest.json",
        manifest_hash,
        application,
        COLUMNS,
        2,
        descriptor["effective_schema"],
        policy=policy,
        approved_rules=rules,
    )


def test_v2_state_verified():
    artifacts, app, policy, descriptor, checksum = setup_state()

    frame = read_state(
        artifacts, app, policy, descriptor, checksum
    )

    assert len(frame) == 2
    assert str(frame.loc[0, "amount"]) == (
        "9007199254740993.0100"
    )


def test_v2_requires_approved_rules():
    artifacts, app, policy, descriptor, checksum = setup_state()

    with pytest.raises(ValueError):
        read_state(
            artifacts,
            app,
            policy,
            descriptor,
            checksum,
            rules=None,
        )


def test_v2_rejects_changed_rule_scale():
    artifacts, app, policy, descriptor, checksum = setup_state()

    changed_rules = [
        (
            None,
            39,
            "amount",
            "DATA_TYPE",
            {
                "type": "DECIMAL",
                "mode": "EXACT",
                "precision": 20,
                "scale": 5,
            },
            True,
        )
    ]

    with pytest.raises(ValueError):
        read_state(
            artifacts,
            app,
            policy,
            descriptor,
            checksum,
            rules=changed_rules,
        )


def test_v2_rejects_wrong_application_pin():
    artifacts, app, policy, descriptor, checksum = setup_state()

    changed = {
        **app,
        "policy_id": 6,
    }

    with pytest.raises(ValueError):
        read_state(
            artifacts,
            changed,
            policy,
            descriptor,
            checksum,
        )


def test_v2_rejects_unsupported_load_mode():
    artifacts, app, policy, descriptor, checksum = setup_state()

    with pytest.raises(ValueError):
        read_state(
            artifacts,
            app,
            {
                **policy,
                "load_strategy": "UPSERT",
            },
            descriptor,
            checksum,
        )