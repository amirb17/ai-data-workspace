from decimal import Decimal

import pandas as pd
import pytest

from app.processing.exact_state_contract import (
    validate_exact_state_contract,
)


def rule(precision=20, scale=4):
    return (
        None, 39, "amount", "DATA_TYPE",
        {
            "type": "DECIMAL",
            "mode": "EXACT",
            "precision": precision,
            "scale": scale,
        },
        True,
    )


def validate(values, precision=20, scale=4):
    frame = pd.DataFrame({
        "amount": pd.Series(values, dtype=object),
    })

    return validate_exact_state_contract(
        frame,
        [rule(precision, scale)],
        ["amount"],
    )


def test_valid_exact_state():
    result = validate([
        Decimal("9007199254740993.0100"),
        Decimal("1.2500"),
        None,
    ])

    assert result["schema_version"] == 2

    assert result["exact_columns"] == [{
        "name": "amount",
        "logical_type": "DECIMAL_EXACT",
        "physical_type": "BIGNUMERIC",
        "precision": 20,
        "scale": 4,
    }]

    assert len(result["sha256"]) == 64


def test_contract_hash_is_deterministic():
    values = [Decimal("1.2500")]

    assert validate(values)["sha256"] == validate(values)["sha256"]


def test_precision_scale_changes_contract_identity():
    values = [Decimal("1.2500")]

    first = validate(values, 20, 4)
    second = validate(values, 20, 5)

    assert first["sha256"] != second["sha256"]


def test_binary_float_is_rejected():
    with pytest.raises(ValueError, match="non-Decimal"):
        validate([1.25])


def test_excessive_scale_is_rejected():
    with pytest.raises(ValueError):
        validate([Decimal("1.23456")])


def test_bigquery_integer_overflow_is_rejected():
    # Construct the exact value directly without quantize().
    # 39 integer digits exceed BigQuery BIGNUMERIC's
    # supported 38 integer digits.
    value = Decimal("9" * 39 + ".0000")

    with pytest.raises(ValueError, match="BigQuery"):
        validate(
            [value],
            precision=76,
            scale=4,
        )

def test_all_null_values_require_explicit_contract():
    result = validate([None, None])

    assert result["exact_columns"][0]["scale"] == 4


def test_missing_business_column_is_rejected():
    with pytest.raises(ValueError):
        validate_exact_state_contract(
            pd.DataFrame({"other": [1]}),
            [rule()],
            ["amount"],
        )