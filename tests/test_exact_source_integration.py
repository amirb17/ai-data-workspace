import hashlib
import io

import pandas as pd
import pytest

from app.processing import silver_processor
from app.processing.exact_source import (
    verified_exact_lexical_frame,
)
from app.processing.schema_fingerprint import calculate_schema_hash
from app.storage.s3_service import BUCKET_NAME


FILE_ID = 7
DATASET_VERSION_ID = 39
RULE_VERSION = 1

RAW_KEY = "raw/test-exact.csv"
BRONZE_KEY = "bronze/file_id=7/data.parquet"
LEXICAL_KEY = "bronze/file_id=7/raw_text.parquet"


class Storage:
    def __init__(self):
        self.objects = {}

    def get_object(self, *, Bucket, Key):
        return {
            "Body": io.BytesIO(self.objects[Key])
        }

    def put_object(
        self,
        *,
        Bucket,
        Key,
        Body,
        **kwargs,
    ):
        self.objects[Key] = Body
        return {}


def parquet(frame):
    buffer = io.BytesIO()
    frame.to_parquet(
        buffer,
        index=False,
        engine="pyarrow",
    )
    return buffer.getvalue()


def fixture_data():
    storage = Storage()

    raw = (
        "return_id,refund_amount\n"
        "R001,9007199254740993.0100\n"
        "R002,1.2500\n"
    ).encode()

    inferred = pd.read_csv(
        io.BytesIO(raw)
    )

    lexical = pd.read_csv(
        io.BytesIO(raw),
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )

    bronze = inferred.copy()
    bronze["_source_file_id"] = FILE_ID
    bronze["_bronze_ingested_at"] = pd.Timestamp(
        "2026-10-27T00:00:00Z"
    )

    storage.objects[RAW_KEY] = raw
    storage.objects[BRONZE_KEY] = parquet(bronze)
    storage.objects[LEXICAL_KEY] = parquet(lexical)

    physical = (
        FILE_ID,
        "test-exact.csv",
        len(raw),
        hashlib.sha256(raw).hexdigest(),
        f"s3://{BUCKET_NAME}/{RAW_KEY}",
        "UPLOADED",
        calculate_schema_hash(inferred),
    )

    return storage, bronze, physical


def verify(storage, bronze, physical):
    return verified_exact_lexical_frame(
        file_id=FILE_ID,
        physical_file=physical,
        bronze_frame=bronze,
        client=storage,
        bucket=BUCKET_NAME,
    )


def test_verified_lexical_frame():
    storage, bronze, physical = fixture_data()

    lexical = verify(
        storage,
        bronze,
        physical,
    )

    assert lexical["refund_amount"].tolist() == [
        "9007199254740993.0100",
        "1.2500",
    ]


def test_modified_source_is_rejected():
    storage, bronze, physical = fixture_data()

    storage.objects[RAW_KEY] = (
        storage.objects[RAW_KEY].replace(
            b"1.2500",
            b"1.3500",
        )
    )

    with pytest.raises(ValueError, match="SHA-256"):
        verify(storage, bronze, physical)


def test_modified_lexical_sidecar_is_rejected():
    storage, bronze, physical = fixture_data()

    changed = pd.DataFrame({
        "return_id": ["R001", "R002"],
        "refund_amount": ["999.0000", "1.2500"],
    })

    storage.objects[LEXICAL_KEY] = parquet(changed)

    with pytest.raises(ValueError, match="sidecar"):
        verify(storage, bronze, physical)


def test_bronze_row_reordering_is_rejected():
    storage, bronze, physical = fixture_data()

    reordered = bronze.iloc[::-1].reset_index(drop=True)

    with pytest.raises(ValueError, match="Bronze rows"):
        verify(storage, reordered, physical)


def test_exact_silver_uses_approved_verified_source(monkeypatch):
    storage, bronze, physical = fixture_data()

    rules = [
        (
            None,
            DATASET_VERSION_ID,
            "refund_amount",
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

    monkeypatch.setattr(
        silver_processor,
        "s3_client",
        storage,
    )

    monkeypatch.setattr(
        silver_processor,
        "get_approved_policy",
        lambda *args: rules,
    )

    monkeypatch.setattr(
        silver_processor,
        "get_physical_file_by_id",
        lambda file_id: physical,
    )

    result = silver_processor.process_silver(
        file_id=FILE_ID,
        dataset_version_id=DATASET_VERSION_ID,
        rule_version=RULE_VERSION,
        bucket_name=BUCKET_NAME,
        approved_rules=rules,
    )

    assert result["valid_rows"] == 2
    assert result["rejected_rows"] == 0

    values = result["silver_df"][
        "refund_amount"
    ].tolist()

    assert str(values[0]) == "9007199254740993.0100"
    assert str(values[1]) == "1.2500"

    stored_silver = pd.read_parquet(
        io.BytesIO(
            storage.objects[result["silver_key"]]
        )
    )

    assert str(
        stored_silver.loc[0, "refund_amount"]
    ) == "9007199254740993.0100"