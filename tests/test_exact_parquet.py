from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.processing.exact_parquet import (
    read_exact_parquet,
    write_exact_parquet,
)


def rules(precision=20, scale=4):
    return [
        (
            None,
            39,
            "amount",
            "DATA_TYPE",
            {
                "type": "DECIMAL",
                "mode": "EXACT",
                "precision": precision,
                "scale": scale,
            },
            True,
        )
    ]


def frame(values):
    return pd.DataFrame({
        "order_id": ["O001", "O002"][:len(values)],
        "amount": pd.Series(values, dtype=object),
    })


def test_decimal_round_trip():
    original = frame([
        Decimal("9007199254740993.0100"),
        Decimal("1.2500"),
    ])

    payload, contract = write_exact_parquet(
        original,
        rules(),
        ["order_id", "amount"],
    )

    restored = read_exact_parquet(
        payload,
        contract,
        rules(),
        ["order_id", "amount"],
        expected_columns=["order_id", "amount"],
    )

    assert restored["amount"].tolist() == [
        Decimal("9007199254740993.0100"),
        Decimal("1.2500"),
    ]

    assert (
        restored.loc[0, "amount"].as_tuple().exponent
        == -4
    )


def test_physical_arrow_decimal128():
    payload, _ = write_exact_parquet(
        frame([Decimal("1.2500")]),
        rules(),
        ["order_id", "amount"],
    )

    schema = pq.read_schema(pa.BufferReader(payload))

    assert schema.field("amount").type == pa.decimal128(20, 4)


def test_physical_arrow_decimal256():
    big_value = Decimal(
        "123456789012345678901234567890.1234"
    )

    payload, _ = write_exact_parquet(
        frame([big_value]),
        rules(40, 4),
        ["order_id", "amount"],
    )

    schema = pq.read_schema(pa.BufferReader(payload))

    assert schema.field("amount").type == pa.decimal256(40, 4)


def test_all_null_exact_column_stays_decimal():
    payload, contract = write_exact_parquet(
        frame([None, None]),
        rules(),
        ["order_id", "amount"],
    )

    schema = pq.read_schema(pa.BufferReader(payload))

    assert schema.field("amount").type == pa.decimal128(20, 4)

    restored = read_exact_parquet(
        payload,
        contract,
        rules(),
        ["order_id", "amount"],
        expected_columns=["order_id", "amount"],
    )

    assert restored["amount"].isna().all()


def test_float_is_never_promoted_to_exact():
    with pytest.raises(ValueError):
        write_exact_parquet(
            frame([1.25]),
            rules(),
            ["order_id", "amount"],
        )


def test_contract_scale_mismatch_rejected():
    payload, contract = write_exact_parquet(
        frame([Decimal("1.2500")]),
        rules(),
        ["order_id", "amount"],
    )

    with pytest.raises(ValueError):
        read_exact_parquet(
            payload,
            contract,
            rules(20, 5),
            ["order_id", "amount"],
            expected_columns=["order_id", "amount"],
        )


def test_column_order_mismatch_rejected():
    payload, contract = write_exact_parquet(
        frame([Decimal("1.2500")]),
        rules(),
        ["order_id", "amount"],
    )

    with pytest.raises(ValueError):
        read_exact_parquet(
            payload,
            contract,
            rules(),
            ["order_id", "amount"],
            expected_columns=["amount", "order_id"],
        )


def test_legacy_float_not_accepted_as_exact():
    with pytest.raises(ValueError):
        write_exact_parquet(
            frame([1.25]),
            rules(),
            ["order_id", "amount"],
        )