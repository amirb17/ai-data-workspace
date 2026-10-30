import hashlib
import io
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.processing.exact_gold_parquet import (
    write_exact_gold_parquet,
    read_exact_gold_parquet,
)


def test_exact_gold_base_round_trip():
    frame = pd.DataFrame({
        "order_id": ["O001", "O002"],
        "amount": [
            Decimal("0.1000"),
            Decimal("1234567890123456.0001"),
        ],
    })

    payload, descriptor = write_exact_gold_parquet(
        frame,
        {"amount": {"precision": 20, "scale": 4}},
    )

    assert descriptor["format_version"] == 2

    schema = pq.read_schema(io.BytesIO(payload))

    assert schema.field("amount").type == (
        pa.decimal128(20, 4)
    )

    restored = read_exact_gold_parquet(
        payload, descriptor
    )

    assert restored["amount"].tolist() == (
        frame["amount"].tolist()
    )


def test_exact_gold_aggregates_preserve_physical_types():
    frame = pd.DataFrame({
        "category": ["retail"],
        "amount_sum": [
            Decimal("1234567890123456.1001")
        ],
        "amount_mean": [
            Decimal("617283945061728.05005")
        ],
        "amount_min": [
            Decimal("0.1000")
        ],
        "amount_max": [
            Decimal("1234567890123456.0001")
        ],
        "record_count": [2],
    })

    contracts = {
        "amount_sum": {"precision": 21, "scale": 4},
        "amount_mean": {"precision": 21, "scale": 5},
        "amount_min": {"precision": 20, "scale": 4},
        "amount_max": {"precision": 20, "scale": 4},
    }

    payload, descriptor = write_exact_gold_parquet(
        frame, contracts
    )

    schema = pq.read_schema(io.BytesIO(payload))

    assert schema.field("amount_mean").type == (
        pa.decimal128(21, 5)
    )

    restored = read_exact_gold_parquet(
        payload, descriptor
    )

    for column in contracts:
        assert restored[column].tolist() == (
            frame[column].tolist()
        )

    assert restored["record_count"].tolist() == [2]


def test_gold_decimal_scale_violation_rejected():
    frame = pd.DataFrame({
        "amount_mean": [
            Decimal("617283945061728.05005")
        ],
    })

    with pytest.raises(ValueError, match="scale"):
        write_exact_gold_parquet(
            frame,
            {
                "amount_mean": {
                    "precision": 21,
                    "scale": 4,
                }
            },
        )


def test_gold_decimal_float_contamination_rejected():
    frame = pd.DataFrame({
        "amount": [
            Decimal("1.2500"),
            1.25,
        ],
    })

    with pytest.raises(
        ValueError,
        match="non-Decimal",
    ):
        write_exact_gold_parquet(
            frame,
            {"amount": {"precision": 20, "scale": 4}},
        )


def test_gold_physical_decimal_type_mismatch_rejected():
    frame = pd.DataFrame({
        "amount": [Decimal("1.2500")],
    })

    _, descriptor = write_exact_gold_parquet(
        frame,
        {"amount": {"precision": 20, "scale": 4}},
    )

    # Construct a valid Parquet file with the wrong
    # physical precision, then update the checksum.
    wrong_table = pa.table({
        "amount": pa.array(
            [Decimal("1.2500")],
            type=pa.decimal128(21, 4),
        )
    })

    buffer = io.BytesIO()
    pq.write_table(wrong_table, buffer)
    wrong_payload = buffer.getvalue()

    descriptor["sha256"] = hashlib.sha256(
        wrong_payload
    ).hexdigest()

    with pytest.raises(
        ValueError,
        match="physical Decimal type",
    ):
        read_exact_gold_parquet(
            wrong_payload, descriptor
        )


def test_all_null_decimal_keeps_approved_type():
    frame = pd.DataFrame({
        "amount": pd.Series(
            [None, None],
            dtype=object,
        ),
    })

    payload, descriptor = write_exact_gold_parquet(
        frame,
        {"amount": {"precision": 20, "scale": 4}},
    )

    assert pq.read_schema(
        io.BytesIO(payload)
    ).field("amount").type == pa.decimal128(20, 4)

    restored = read_exact_gold_parquet(
        payload, descriptor
    )

    assert restored["amount"].isna().all()