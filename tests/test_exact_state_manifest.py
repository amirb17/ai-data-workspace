import io
from decimal import Decimal

import pandas as pd
import pytest

from app.storage.incremental_artifacts import (
    IncrementalArtifacts,
)


RULES = [
    (
        None,
        39,
        "amount",
        "DATA_TYPE",
        {
            "type": "DECIMAL",
            "mode": "EXACT",
            "precision": 20,
            "scale": 4,
        },
        True,
    )
]

COLUMNS = ["order_id", "amount"]

PINS = {
    "dataset_version_id": 39,
    "policy_id": 5,
    "rule_version": 1,
}


class Store:
    def __init__(self):
        self.objects = {}

    def put_object(
        self,
        *,
        Bucket,
        Key,
        Body,
        IfNoneMatch=None,
    ):
        assert IfNoneMatch == "*"
        assert Key not in self.objects
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        return {
            "Body": io.BytesIO(self.objects[Key])
        }


def sample():
    return pd.DataFrame({
        "order_id": ["O001", "O002"],
        "amount": pd.Series(
            [
                Decimal("9007199254740993.0100"),
                Decimal("1.2500"),
            ],
            dtype=object,
        ),
    })


def test_exact_state_immutable_roundtrip():
    store = Store()
    artifacts = IncrementalArtifacts(store)

    frame = sample()

    checksum, descriptor = artifacts.put_exact_frame(
        "test/state.parquet",
        frame,
        RULES,
        COLUMNS,
        **PINS,
    )

    assert descriptor["manifest_version"] == 2

    assert descriptor["effective_schema"] == [
        {"name": "order_id", "data_type": "TEXT"},
        {
            "name": "amount",
            "data_type": "DECIMAL_EXACT",
            "precision": 20,
            "scale": 4,
        },
    ]

    restored = artifacts.read_exact_frame(
        "test/state.parquet",
        checksum,
        descriptor,
        RULES,
        COLUMNS,
        expected_columns=COLUMNS,
        **PINS,
    )

    assert restored["amount"].tolist() == (
        frame["amount"].tolist()
    )


def test_tampered_artifact_rejected():
    store = Store()
    artifacts = IncrementalArtifacts(store)

    checksum, descriptor = artifacts.put_exact_frame(
        "test/state.parquet",
        sample(),
        RULES,
        COLUMNS,
        **PINS,
    )

    store.objects["test/state.parquet"] += b"changed"

    with pytest.raises(ValueError):
        artifacts.read_exact_frame(
            "test/state.parquet",
            checksum,
            descriptor,
            RULES,
            COLUMNS,
            expected_columns=COLUMNS,
            **PINS,
        )


def test_rule_scale_drift_rejected():
    store = Store()
    artifacts = IncrementalArtifacts(store)

    checksum, descriptor = artifacts.put_exact_frame(
        "test/state.parquet",
        sample(),
        RULES,
        COLUMNS,
        **PINS,
    )

    modified_rules = [
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
        artifacts.read_exact_frame(
            "test/state.parquet",
            checksum,
            descriptor,
            modified_rules,
            COLUMNS,
            expected_columns=COLUMNS,
            **PINS,
        )


def test_source_policy_pin_mismatch_rejected():
    store = Store()
    artifacts = IncrementalArtifacts(store)

    checksum, descriptor = artifacts.put_exact_frame(
        "test/state.parquet",
        sample(),
        RULES,
        COLUMNS,
        **PINS,
    )

    with pytest.raises(ValueError):
        artifacts.read_exact_frame(
            "test/state.parquet",
            checksum,
            descriptor,
            RULES,
            COLUMNS,
            dataset_version_id=39,
            policy_id=6,
            rule_version=1,
            expected_columns=COLUMNS,
        )


def test_legacy_float_cannot_enter_v2_state():
    store = Store()
    artifacts = IncrementalArtifacts(store)

    frame = sample()
    frame["amount"] = [1.25, 2.5]

    with pytest.raises(ValueError):
        artifacts.put_exact_frame(
            "test/invalid.parquet",
            frame,
            RULES,
            COLUMNS,
            **PINS,
        )

    assert "test/invalid.parquet" not in store.objects


def test_v1_descriptor_cannot_be_read_as_v2():
    store = Store()
    artifacts = IncrementalArtifacts(store)

    checksum, descriptor = artifacts.put_exact_frame(
        "test/state.parquet",
        sample(),
        RULES,
        COLUMNS,
        **PINS,
    )

    changed = {
        **descriptor,
        "manifest_version": 1,
    }

    with pytest.raises(ValueError):
        artifacts.read_exact_frame(
            "test/state.parquet",
            checksum,
            changed,
            RULES,
            COLUMNS,
            expected_columns=COLUMNS,
            **PINS,
        )