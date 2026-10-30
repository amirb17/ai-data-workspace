from decimal import Decimal

import io

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.processing import silver_processor


RULES = [
    (
        None,
        39,
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


class Storage:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):
        return {
            "Body": io.BytesIO(self.objects[Key])
        }


def test_exact_silver_writer_preserves_decimal_type(
    monkeypatch,
):
    storage = Storage()

    monkeypatch.setattr(
        silver_processor,
        "s3_client",
        storage,
    )

    frame = pd.DataFrame({
        "return_id": ["R001"],
        "refund_amount": pd.Series(
            [Decimal("9007199254740993.0100")],
            dtype=object,
        ),
    })

    contract = silver_processor._write_exact_parquet_to_s3(
        df=frame,
        bucket_name="test",
        key="silver/exact.parquet",
        approved_rules=RULES,
        business_columns=["return_id", "refund_amount"],
    )

    schema = pq.read_schema(
        pa.BufferReader(
            storage.objects["silver/exact.parquet"]
        )
    )

    assert schema.field("refund_amount").type == (
        pa.decimal128(20, 4)
    )

    assert contract["schema_version"] == 2


def test_all_null_exact_column_remains_decimal(
    monkeypatch,
):
    storage = Storage()

    monkeypatch.setattr(
        silver_processor,
        "s3_client",
        storage,
    )

    frame = pd.DataFrame({
        "return_id": ["R001", "R002"],
        "refund_amount": pd.Series(
            [None, None],
            dtype=object,
        ),
    })

    silver_processor._write_exact_parquet_to_s3(
        df=frame,
        bucket_name="test",
        key="silver/nulls.parquet",
        approved_rules=RULES,
        business_columns=["return_id", "refund_amount"],
    )

    schema = pq.read_schema(
        pa.BufferReader(
            storage.objects["silver/nulls.parquet"]
        )
    )

    assert schema.field("refund_amount").type == (
        pa.decimal128(20, 4)
    )


def test_modified_storage_readback_is_rejected(
    monkeypatch,
):
    class CorruptingStorage(Storage):
        def put_object(
            self, *, Bucket, Key, Body, **kwargs
        ):
            self.objects[Key] = Body + b"corrupt"

    storage = CorruptingStorage()

    monkeypatch.setattr(
        silver_processor,
        "s3_client",
        storage,
    )

    frame = pd.DataFrame({
        "return_id": ["R001"],
        "refund_amount": pd.Series(
            [Decimal("1.2500")],
            dtype=object,
        ),
    })

    with pytest.raises(ValueError, match="readback"):
        silver_processor._write_exact_parquet_to_s3(
            df=frame,
            bucket_name="test",
            key="silver/corrupted.parquet",
            approved_rules=RULES,
            business_columns=["return_id", "refund_amount"],
        )