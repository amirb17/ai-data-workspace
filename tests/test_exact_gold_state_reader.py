import hashlib
import json
from decimal import Decimal

import pandas as pd
import pytest

from app.services import exact_gold_state_reader as reader
from app.services.dataset_analytics_service import load_pinned_state


RULES = [
    (
        None, 39, "amount", "DATA_TYPE",
        {
            "type": "DECIMAL",
            "mode": "EXACT",
            "precision": 20,
            "scale": 4,
        },
        True,
    )
]


@pytest.fixture
def trusted(monkeypatch):
    monkeypatch.setenv("RUN_DB_TESTS", "1")
    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY", "1"
    )
    monkeypatch.setattr(
        reader,
        "get_approved_policy",
        lambda dataset_version_id, rule_version:
            RULES if (dataset_version_id, rule_version) == (39, 1)
            else [],
    )

    application = {
        "application_id": 9,
        "dataset_id": 88,
        "dataset_version_id": 39,
        "policy_id": 5,
        "applied_rule_version": 1,
    }

    policy = {
        "policy_id": 5,
        "dataset_version_id": 39,
        "load_strategy": "APPEND",
        "schema_columns": [{"name": "amount"}],
    }

    manifest = {
        **application,
        "manifest_version": 2,
        "effective_schema": [
            {"name": "amount", "data_type": "DECIMAL"}
        ],
    }

    payload = json.dumps(manifest).encode()

    state = {
        "state_id": 100,
        "dataset_id": 88,
        "dataset_version_id": 39,
        "policy_id": 5,
        "source_application_id": 9,
        "status": "PUBLISHED",
        "manifest_key": "state/manifest.json",
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
        "row_count": 1,
    }

    class Store:
        def __init__(self):
            self.payload = payload
            self.called = False

        def read(self, key):
            assert key == state["manifest_key"]
            return self.payload

        def validate_state(self, *args, **kwargs):
            self.called = True
            assert kwargs["approved_rules"] == RULES
            assert kwargs["policy"] == policy
            return pd.DataFrame({
                "amount": [Decimal("1.2500")]
            })

    return Store(), state, application, policy


def test_v2_reader_uses_pinned_approved_rules(trusted):
    store, state, application, policy = trusted

    loaded_state, frame, approved = (
        reader.read_pinned_exact_state(
            store, state, application, policy
        )
    )

    assert loaded_state == state
    assert frame["amount"].iloc[0] == Decimal("1.2500")
    assert approved == RULES
    assert store.called


def test_corrupted_v2_state_manifest_rejected(trusted):
    store, state, application, policy = trusted
    store.payload = b"corrupted"

    with pytest.raises(ValueError, match="integrity"):
        reader.read_pinned_exact_state(
            store, state, application, policy
        )

    assert not store.called


def test_wrong_v2_manifest_version_rejected(trusted):
    store, state, application, policy = trusted

    manifest = json.loads(store.payload)
    manifest["manifest_version"] = 1

    store.payload = json.dumps(manifest).encode()
    state["manifest_sha256"] = hashlib.sha256(
        store.payload
    ).hexdigest()

    with pytest.raises(ValueError, match="manifest V2"):
        reader.read_pinned_exact_state(
            store, state, application, policy
        )

    assert not store.called


def test_missing_approved_rules_rejected(trusted, monkeypatch):
    store, state, application, policy = trusted

    monkeypatch.setattr(
        reader,
        "get_approved_policy",
        lambda *args: [],
    )

    with pytest.raises(ValueError, match="Approved"):
        reader.read_pinned_exact_state(
            store, state, application, policy
        )

    assert not store.called


def test_v2_cannot_enter_legacy_gold_refresh(trusted):
    store, state, application, policy = trusted

    with pytest.raises(ValueError, match="not enabled"):
        load_pinned_state(
            store, state, application, policy
        )

    assert not store.called