import hashlib
import io
import json
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.services import append_application_service as execution
from app.processing.exact_parquet import write_exact_parquet
from app.storage.incremental_artifacts import IncrementalArtifacts
from tests.test_exact_state_manifest import Store, RULES, COLUMNS


SOURCE = (
    "silver/dataset_version_id=39/"
    "file_id=7/rule_version=1/data.parquet"
)

APP = {
    "application_id": 9,
    "dataset_id": 88,
    "dataset_version_id": 39,
    "policy_id": 5,
    "upload_request_id": 17,
    "applied_rule_version": 1,
    "source_dq_run_id": 11,
    "input_rows": 1,
    "valid_rows": 1,
    "rejected_rows": 0,
}

POLICY = {
    "load_strategy": "APPEND",
    "normalization_version": 1,
    "business_keys": [],
    "schema_columns": [
        {"name": "order_id", "data_type": "TEXT"},
        {"name": "amount", "data_type": "DECIMAL"},
    ],
    "event_time_column": None,
}


@pytest.fixture
def delivery(monkeypatch):
    storage = Store()
    artifacts = IncrementalArtifacts(storage)

    source_frame = pd.DataFrame({
        "order_id": ["O001"],
        "amount": pd.Series(
            [Decimal("1.2500")],
            dtype=object,
        ),
    })

    payload, _ = write_exact_parquet(
        source_frame, RULES, COLUMNS
    )
    storage.objects[SOURCE] = payload

    saved = {
    "delivery": None,
    "silver_sha256": hashlib.sha256(payload).hexdigest(),
}

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self, **kwargs):
            return self

        def execute(self, sql, params):
            if "FROM delivery_manifests" in sql:
                self.result = saved["delivery"]

            elif "FROM data_quality_runs" in sql:
                self.result = {
                    "silver_path": SOURCE,
                    "silver_sha256": saved["silver_sha256"],
                }

            elif "INSERT INTO delivery_manifests" in sql:
                manifest = json.loads(
                    storage.objects[params[1]]
                )

                saved["delivery"] = {
                    "manifest_id": 1,
                    "manifest_key": params[1],
                    "manifest_sha256": params[2],
                    "silver_key": params[3],
                    "silver_sha256": params[4],
                    "effective_schema": manifest["effective_schema"],
                    "manifest_version": manifest["manifest_version"],
                }

                self.result = saved["delivery"]
            else:
                raise AssertionError("Unexpected SQL")

            return self

        def fetchone(self):
            return self.result

    monkeypatch.setattr(
        execution, "get_connection", Database
    )
    monkeypatch.setattr(
        execution, "repository_transaction", Database
    )

    return artifacts, storage, saved


def test_exact_delivery_and_immutable_retry(delivery):
    artifacts, storage, saved = delivery

    first, frame = execution.delivery_manifest(
        artifacts, APP, POLICY, 7, exact_rules=RULES
    )

    assert str(frame.loc[0, "amount"]) == "1.2500"

    manifest = json.loads(
        storage.objects[first["manifest_key"]]
    )

    assert manifest["manifest_version"] == 2
    assert manifest["exact_descriptor"]["manifest_version"] == 2

    keys_before = set(storage.objects)

    second, repeated = execution.delivery_manifest(
        artifacts, APP, POLICY, 7, exact_rules=RULES
    )

    assert second == first
    assert set(storage.objects) == keys_before
    assert repeated["amount"].tolist() == frame["amount"].tolist()


def test_v1_delivery_cannot_replay_as_v2(delivery):
    artifacts, storage, saved = delivery

    first, _ = execution.delivery_manifest(
        artifacts, APP, POLICY, 7, exact_rules=RULES
    )

    key = first["manifest_key"]
    manifest = json.loads(storage.objects[key])
    manifest["manifest_version"] = 1

    # Simulate internally consistent but wrong-version
    # historical metadata.
    modified = artifacts.json(manifest)
    storage.objects[key] = modified

    saved["delivery"]["manifest_sha256"] = (
        hashlib.sha256(modified).hexdigest()
    )

    with pytest.raises(ValueError, match="mixing"):
        execution.delivery_manifest(
            artifacts, APP, POLICY, 7, exact_rules=RULES
        )


def test_wrong_arrow_decimal_type_rejected(delivery):
    artifacts, storage, saved = delivery

    # Logical value is valid, but the physical Parquet
    # type does not match the approved P20:S4 contract.
    table = pa.table({
        "order_id": pa.array(["O001"]),
        "amount": pa.array(
            [Decimal("1.2500")],
            type=pa.decimal128(21, 4),
        ),
    })

    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    storage.objects[SOURCE] = buffer.getvalue()
        # Update the synthetic DQ checksum so this test
    # reaches the physical Arrow type validation.
    saved["silver_sha256"] = hashlib.sha256(
        buffer.getvalue()
    ).hexdigest()
    with pytest.raises(ValueError, match="Physical"):
        execution.delivery_manifest(
            artifacts, APP, POLICY, 7, exact_rules=RULES
        )