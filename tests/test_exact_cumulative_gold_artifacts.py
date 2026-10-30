import io
from decimal import Decimal

import pandas as pd
import pytest

from app.processing.cumulative_gold import build_cumulative_gold
from app.storage.exact_cumulative_gold_artifacts import (
    ExactCumulativeGoldArtifacts,
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

POLICY = {
    "load_strategy": "APPEND",
    "schema_columns": [
        {"name": "order_id"},
        {"name": "category"},
        {"name": "amount"},
    ],
    "business_keys": [],
}

RUN = {
    "dataset_id": 88,
    "source_state_id": 100,
    "gold_run_id": 200,
    "dataset_version_id": 39,
    "policy_id": 5,
    "source_sha256": "a" * 64,
    "build_version": 1,
}


class MemoryS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(
        self,
        Bucket,
        Key,
        Body,
        IfNoneMatch=None,
        **kwargs,
    ):
        assert IfNoneMatch == "*"

        if Key in self.objects:
            raise ValueError("Immutable object overwritten")

        self.objects[Key] = bytes(Body)
        return {}


@pytest.fixture
def exact_gold(monkeypatch):
    monkeypatch.setenv("RUN_DB_TESTS", "1")
    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY",
        "1",
    )

    storage = MemoryS3()
    store = ExactCumulativeGoldArtifacts(storage)

    state = pd.DataFrame({
        "order_id": ["O001", "O002"],
        "category": ["retail", "retail"],
        "amount": [
            Decimal("0.1000"),
            Decimal("1234567890123456.0001"),
        ],
    })

    artifacts = build_cumulative_gold(state, POLICY)

    return store, storage, artifacts


def test_exact_gold_immutable_build_and_readback(exact_gold):
    store, storage, artifacts = exact_gold

    key, checksum, catalog = store.build_exact(
        RUN,
        artifacts,
        RULES,
        {"amount": 5},
    )

    verified = store.validate_exact(
        RUN,
        key,
        checksum,
        expected_rows=2,
        columns=["order_id", "category", "amount"],
        approved_rules=RULES,
        avg_scales={"amount": 5},
    )

    assert verified == catalog
    assert verified[0]["exact_parquet"][
        "exact_columns"
    ]["amount"] == {
        "precision": 20,
        "scale": 4,
    }

    assert verified[0]["row_count"] == 2


def test_exact_gold_corrupted_object_rejected(exact_gold):
    store, storage, artifacts = exact_gold

    key, checksum, catalog = store.build_exact(
        RUN,
        artifacts,
        RULES,
        {"amount": 5},
    )

    artifact_key = catalog[0]["storage_path"]

    storage.objects[artifact_key] = b"corrupted"

    with pytest.raises(ValueError, match="checksum"):
        store.validate_exact(
            RUN,
            key,
            checksum,
            2,
            ["order_id", "category", "amount"],
            RULES,
            {"amount": 5},
        )


def test_exact_gold_unapproved_source_contract_rejected(
    exact_gold,
):
    store, storage, artifacts = exact_gold

    key, checksum, _ = store.build_exact(
        RUN,
        artifacts,
        RULES,
        {"amount": 5},
    )

    changed_rules = [
        (
            None,
            39,
            "amount",
            "DATA_TYPE",
            {
                "type": "DECIMAL",
                "mode": "EXACT",
                "precision": 21,
                "scale": 4,
            },
            True,
        )
    ]

    with pytest.raises(
        ValueError,
        match="approved contracts",
    ):
        store.validate_exact(
            RUN,
            key,
            checksum,
            2,
            ["order_id", "category", "amount"],
            changed_rules,
            {"amount": 5},
        )


def test_exact_gold_test_gate_required(exact_gold, monkeypatch):
    store, storage, artifacts = exact_gold

    monkeypatch.delenv(
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY"
    )

    with pytest.raises(
        ValueError,
        match="restricted to isolated tests",
    ):
        store.build_exact(
            RUN,
            artifacts,
            RULES,
            {"amount": 5},
        )