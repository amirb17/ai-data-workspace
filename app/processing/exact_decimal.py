"""Exact decimal primitives.

This module never converts binary floating-point values into Decimal.

Exact decimal evidence must originate from lexical source text or an
already-exact Decimal value. Precision/scale validation never rounds.
"""

from dataclasses import dataclass
from decimal import (
    Decimal,
    InvalidOperation,
    localcontext,
)
import re


MAX_BIGNUMERIC_PRECISION = 76
MAX_BIGNUMERIC_SCALE = 38

MAX_DECIMAL_TEXT_LENGTH = 256


_DECIMAL_TEXT = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$"
)


@dataclass(
    frozen=True,
    slots=True,
)
class ExactDecimalSpec:
    precision: int
    scale: int

    def __post_init__(self):
        if (
            type(self.precision) is not int
            or type(self.scale) is not int
        ):
            raise ValueError(
                "Decimal precision and scale must be integers"
            )

        if not (
            1
            <= self.precision
            <= MAX_BIGNUMERIC_PRECISION
        ):
            raise ValueError(
                "Decimal precision outside supported envelope"
            )

        if not (
            0
            <= self.scale
            <= MAX_BIGNUMERIC_SCALE
        ):
            raise ValueError(
                "Decimal scale outside supported envelope"
            )

        if self.scale > self.precision:
            raise ValueError(
                "Decimal scale cannot exceed precision"
            )


def parse_exact_decimal(
    value: str | Decimal,
    spec: ExactDecimalSpec,
) -> Decimal:
    """
    Parse/validate an exact decimal without binary-float conversion.

    Scientific notation, NaN, infinity and values requiring rounding
    are deliberately rejected.

    The returned Decimal is quantized to the configured scale.
    """

    if isinstance(value, Decimal):
        number = value

    elif isinstance(value, str):
        text = value.strip()

        if (
            not text
            or len(text) > MAX_DECIMAL_TEXT_LENGTH
            or not _DECIMAL_TEXT.fullmatch(text)
        ):
            raise ValueError(
                "Invalid exact decimal lexical value"
            )

        try:
            number = Decimal(text)

        except InvalidOperation:
            raise ValueError(
                "Invalid exact decimal lexical value"
            ) from None

    else:
        # Especially important: never Decimal(str(float)).
        raise ValueError(
            "Exact decimal requires lexical text or Decimal"
        )

    if not number.is_finite():
        raise ValueError(
            "Exact decimal must be finite"
        )

    quantum = Decimal(1).scaleb(
        -spec.scale
    )

    try:
        # Large enough for the complete supported BIGNUMERIC envelope.
        with localcontext() as context:
            context.prec = (
                MAX_BIGNUMERIC_PRECISION
                + MAX_BIGNUMERIC_SCALE
                + 8
            )

            quantized = number.quantize(
                quantum
            )

    except InvalidOperation:
        raise ValueError(
            "Exact decimal exceeds configured precision"
        ) from None

    # Decimal equality ignores insignificant trailing zeros.
    # Therefore 1.2300 is valid for scale=2, while 1.234 is not.
    if quantized != number:
        raise ValueError(
            "Exact decimal exceeds configured scale"
        )

    if quantized == 0:
        integer_digits = 0
    else:
        integer_digits = max(
            quantized.copy_abs().adjusted() + 1,
            0,
        )

    if (
        integer_digits
        > spec.precision - spec.scale
    ):
        raise ValueError(
            "Exact decimal exceeds configured precision"
        )

    return quantized