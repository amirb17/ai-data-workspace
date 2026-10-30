from decimal import Decimal

import pandas as pd

from app.processing.cumulative_gold import build_cumulative_gold
import pytest

POLICY = {
    "load_strategy": "APPEND",
    "schema_columns": [
        {"name": "order_id"},
        {"name": "category"},
        {"name": "amount"},
    ],
    "business_keys": [],
}


def sample_state():
    return pd.DataFrame({
        "order_id": ["O001", "O002"],
        "category": ["retail", "retail"],
        "amount": [
            Decimal("0.1000"),
            Decimal("1234567890123456.0001"),
        ],
    })


def test_exact_gold_base_preserves_decimal_values():
    artifacts = build_cumulative_gold(
        sample_state(),
        POLICY,
    )

    base = artifacts[0][0]

    assert base["amount"].tolist() == [
        Decimal("0.1000"),
        Decimal("1234567890123456.0001"),
    ]

    assert all(
        isinstance(value, Decimal)
        for value in base["amount"]
    )


def test_exact_decimal_is_treated_as_gold_measure():
    artifacts = build_cumulative_gold(
        sample_state(),
        POLICY,
    )

    summary = next(
        (
            frame
            for frame, metadata in artifacts[1:]
            if metadata["artifact_name"]
            == "category_summary"
        ),
        None,
    )

    assert summary is not None, (
        "Exact Decimal must be recognized "
        "as a numeric Gold measure"
    )

    assert summary["amount_sum"].iloc[0] == Decimal(
        "1234567890123456.1001"
    )

    assert isinstance(
        summary["amount_sum"].iloc[0],
        Decimal,
    )

    assert isinstance(
        summary["amount_mean"].iloc[0],
        Decimal,
    )

def test_exact_decimal_repeating_mean_fails_closed():
    frame = pd.DataFrame({
        "order_id": ["O001", "O002", "O003"],
        "category": ["retail", "retail", "retail"],
        "amount": [
            Decimal("1.0000"),
            Decimal("0.0000"),
            Decimal("0.0000"),
        ],
    })

    with pytest.raises(
        ValueError,
        match="rounding policy",
    ):
        build_cumulative_gold(frame, POLICY)


def test_mixed_decimal_and_float_rejected():
    frame = sample_state()

    frame.at[1, "amount"] = 10.5

    with pytest.raises(
        ValueError,
        match="Mixed Decimal",
    ):
        build_cumulative_gold(frame, POLICY)