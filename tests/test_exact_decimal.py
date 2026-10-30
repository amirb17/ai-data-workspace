from decimal import Decimal

import pytest

from app.processing.exact_decimal import (
    ExactDecimalSpec,
    parse_exact_decimal,
)


def test_exact_decimal_preserves_large_value_and_scale():
    spec = ExactDecimalSpec(
        precision=20,
        scale=4,
    )

    value = parse_exact_decimal(
        "9007199254740993.0100",
        spec,
    )

    assert value == Decimal(
        "9007199254740993.0100"
    )

    assert value.as_tuple().exponent == -4


def test_exact_decimal_accepts_trailing_zero_normalization():
    spec = ExactDecimalSpec(
        precision=10,
        scale=2,
    )

    assert parse_exact_decimal(
        "1.2300",
        spec,
    ) == Decimal("1.23")


def test_exact_decimal_rejects_rounding():
    spec = ExactDecimalSpec(
        precision=10,
        scale=2,
    )

    with pytest.raises(ValueError):
        parse_exact_decimal(
            "1.234",
            spec,
        )


def test_exact_decimal_rejects_precision_overflow():
    spec = ExactDecimalSpec(
        precision=7,
        scale=2,
    )

    with pytest.raises(ValueError):
        parse_exact_decimal(
            "123456.78",
            spec,
        )


@pytest.mark.parametrize(
    "value",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "1e3",
        "",
        "abc",
    ],
)
def test_exact_decimal_rejects_invalid_lexical_values(
    value,
):
    spec = ExactDecimalSpec(
        precision=20,
        scale=4,
    )

    with pytest.raises(ValueError):
        parse_exact_decimal(
            value,
            spec,
        )


def test_exact_decimal_never_accepts_binary_float():
    spec = ExactDecimalSpec(
        precision=10,
        scale=2,
    )

    with pytest.raises(ValueError):
        parse_exact_decimal(
            1.23,
            spec,
        )


def test_bigquery_envelope_validation():
    ExactDecimalSpec(
        precision=76,
        scale=38,
    )

    with pytest.raises(ValueError):
        ExactDecimalSpec(
            precision=77,
            scale=2,
        )

    with pytest.raises(ValueError):
        ExactDecimalSpec(
            precision=20,
            scale=39,
        )

    with pytest.raises(ValueError):
        ExactDecimalSpec(
            precision=2,
            scale=3,
        )