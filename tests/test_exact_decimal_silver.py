from decimal import Decimal

import pandas as pd
import pytest

from app.processing.silver_transformer import (
    apply_transformations,
    exact_decimal_spec,
)


def rule(config):
    return (
        1,
        1,
        "amount",
        "DATA_TYPE",
        config,
    )


EXACT = {
    "type": "DECIMAL",
    "mode": "EXACT",
    "precision": 20,
    "scale": 4,
}


def test_exact_decimal_uses_lexical_source():
    # Deliberately simulate a lossy inferred Bronze value.
    inferred = pd.DataFrame({
        "amount": [9007199254740994.0],
    })

    lexical = pd.DataFrame({
        "amount": ["9007199254740993.0100"],
    })

    output, errors, _ = apply_transformations(
        inferred,
        [rule(EXACT)],
        lexical_frame=lexical,
    )

    value = output.loc[0, "amount"]

    assert isinstance(value, Decimal)
    assert value == Decimal(
        "9007199254740993.0100"
    )

    assert value.as_tuple().exponent == -4
    assert not errors["amount:INVALID_NUMERIC"].any()


def test_legacy_decimal_path_is_unchanged():
    frame = pd.DataFrame({
        "amount": ["1.25", "2.50"],
    })

    output, errors, _ = apply_transformations(
        frame,
        [rule({"type": "DECIMAL"})],
    )

    assert output["amount"].tolist() == [
        1.25,
        2.5,
    ]

    assert not errors["amount:INVALID_NUMERIC"].any()


@pytest.mark.parametrize(
    "config",
    [
        {"type": "DECIMAL", "mode": "EXACT"},
        {"type": "DECIMAL", "precision": 20},
        {
            "type": "DECIMAL",
            "mode": "EXACT",
            "precision": 20,
            "scale": 4,
            "extra": True,
        },
        {
            "type": "DECIMAL",
            "mode": "EXACT",
            "precision": True,
            "scale": 4,
        },
        {
            "type": "FLOAT",
            "mode": "EXACT",
            "precision": 20,
            "scale": 4,
        },
    ],
)
def test_invalid_exact_rule_fails_closed(config):
    with pytest.raises(ValueError):
        exact_decimal_spec(config)


def test_exact_decimal_requires_lexical_source():
    frame = pd.DataFrame({
        "amount": [1.25],
    })

    with pytest.raises(ValueError):
        apply_transformations(
            frame,
            [rule(EXACT)],
        )


def test_exact_decimal_rejects_float_lexical_values():
    frame = pd.DataFrame({
        "amount": [1.25],
    })

    with pytest.raises(ValueError):
        apply_transformations(
            frame,
            [rule(EXACT)],
            lexical_frame=frame,
        )


def test_invalid_rows_are_quarantinable():
    lexical = pd.DataFrame({
        "amount": [
            "1.2500",
            "1.23456",
            "invalid",
            "",
        ],
    })

    inferred = pd.DataFrame({
        "amount": [None] * 4,
    })

    output, errors, _ = apply_transformations(
        inferred,
        [rule(EXACT)],
        lexical_frame=lexical,
    )

    assert output.loc[0, "amount"] == Decimal(
        "1.2500"
    )

    assert errors[
        "amount:INVALID_NUMERIC"
    ].tolist() == [
        False,
        True,
        True,
        False,
    ]


def test_exact_decimal_preserves_negative_values():
    lexical = pd.DataFrame({
        "amount": ["-125.0100"],
    })

    inferred = pd.DataFrame({
        "amount": [0.0],
    })

    output, _, _ = apply_transformations(
        inferred,
        [rule(EXACT)],
        lexical_frame=lexical,
    )

    assert output.loc[0, "amount"] == Decimal(
        "-125.0100"
    )