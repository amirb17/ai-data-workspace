import io

import pandas as pd

from app.processing import bronze_processor


class Storage:
    def __init__(self):
        self.objects = {}

    def get_object(
        self,
        *,
        Bucket,
        Key,
    ):
        return {
            "Body": io.BytesIO(
                self.objects[Key]
            )
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


def test_bronze_preserves_raw_decimal_lexical_values(
    monkeypatch,
):
    storage = Storage()

    csv = (
        "id,amount,note\n"
        "001,9007199254740993.0100,null\n"
        "002,0.10,\n"
    ).encode()

    storage.objects[
        "raw/test.csv"
    ] = csv

    monkeypatch.setattr(
        bronze_processor,
        "get_s3_client",
        lambda: storage,
    )

    monkeypatch.setattr(
        bronze_processor,
        "BUCKET_NAME",
        "test-bucket",
    )

    result = (
        bronze_processor.run_bronze_stage(
            file_id=7,
            raw_object_key="raw/test.csv",
        )
    )

    raw_key = (
        "bronze/file_id=7/raw_text.parquet"
    )

    typed_key = (
        "bronze/file_id=7/data.parquet"
    )

    assert raw_key in storage.objects
    assert typed_key in storage.objects

    raw = pd.read_parquet(
        io.BytesIO(
            storage.objects[raw_key]
        )
    )

    assert list(raw.columns) == [
        "id",
        "amount",
        "note",
    ]

    # Leading zeros survive.
    assert raw["id"].tolist() == [
        "001",
        "002",
    ]

    # No float conversion has occurred.
    assert raw["amount"].tolist() == [
        "9007199254740993.0100",
        "0.10",
    ]

    # Source lexical null-like values are preserved.
    # Silver owns null normalization.
    assert raw["note"].tolist() == [
        "null",
        "",
    ]

    # Existing public Bronze result remains present.
    assert result["row_count"] == 2
    assert result["column_names"] == [
        "id",
        "amount",
        "note",
    ]