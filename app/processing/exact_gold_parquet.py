"""Versioned exact-Decimal Gold Parquet serialization.

This module does not publish S3 objects or update metadata.
Every Decimal column requires an explicit physical contract.
"""

import hashlib
import io
from decimal import Decimal
from fractions import Fraction

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


FORMAT_VERSION = 2


def _arrow_type(spec):
    if not isinstance(spec, dict) or set(spec) != {
        "precision",
        "scale",
    }:
        raise ValueError("Invalid Gold Decimal contract")

    precision = spec["precision"]
    scale = spec["scale"]

    if (
        type(precision) is not int
        or type(scale) is not int
        or not 1 <= precision <= 76
        or not 0 <= scale <= precision
    ):
        raise ValueError("Invalid Gold Decimal precision/scale")

    if precision <= 38:
        return pa.decimal128(precision, scale)

    return pa.decimal256(precision, scale)


def _validate_contract(columns, exact_columns):
    if (
        not isinstance(columns, list)
        or not columns
        or len(columns) != len(set(columns))
        or not all(isinstance(c, str) for c in columns)
    ):
        raise ValueError("Invalid Gold column order")

    if not isinstance(exact_columns, dict) or not exact_columns:
        raise ValueError("Exact Gold requires Decimal contracts")

    if not set(exact_columns).issubset(columns):
        raise ValueError("Unknown Exact Gold column")

    for spec in exact_columns.values():
        _arrow_type(spec)


def _decimal_array(series, spec):
    arrow_type = _arrow_type(spec)
    precision = spec["precision"]
    scale = spec["scale"]
    values = []

    for value in series.tolist():
        if value is None or value is pd.NA:
            values.append(None)
            continue

        if not isinstance(value, Decimal):
            raise ValueError(
                "Exact Gold column contains non-Decimal values"
            )

        if not value.is_finite():
            raise ValueError("Non-finite Exact Gold Decimal")

        # Fraction avoids binary floating-point conversion
        # and active Decimal-context rounding.
        scaled = Fraction(value) * (10 ** scale)

        if scaled.denominator != 1:
            raise ValueError(
                "Gold Decimal value exceeds approved scale"
            )

        if abs(scaled.numerator) >= 10 ** precision:
            raise ValueError(
                "Gold Decimal value exceeds approved precision"
            )

        values.append(value)

    return pa.array(values, type=arrow_type)


def write_exact_gold_parquet(frame, exact_columns):
    columns = list(frame.columns)
    _validate_contract(columns, exact_columns)

    arrays = []

    for column in columns:
        if column in exact_columns:
            arrays.append(
                _decimal_array(
                    frame[column],
                    exact_columns[column],
                )
            )
        else:
            # Never let an undeclared Decimal field
            # pass through inferred Arrow serialization.
            if any(
                isinstance(value, Decimal)
                for value in frame[column]
            ):
                raise ValueError(
                    "Undeclared Decimal field in Gold artifact"
                )

            arrays.append(
                pa.array(frame[column], from_pandas=True)
            )

    table = pa.Table.from_arrays(
        arrays,
        names=columns,
    )

    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    payload = buffer.getvalue()

    descriptor = {
        "format_version": FORMAT_VERSION,
        "columns": columns,
        "row_count": len(frame),
        "exact_columns": exact_columns,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

    return payload, descriptor


def read_exact_gold_parquet(payload, descriptor):
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("format_version") != FORMAT_VERSION
    ):
        raise ValueError("Unsupported Gold format version")

    columns = descriptor.get("columns")
    exact_columns = descriptor.get("exact_columns")

    _validate_contract(columns, exact_columns)

    if hashlib.sha256(payload).hexdigest() != descriptor.get(
        "sha256"
    ):
        raise ValueError("Gold Parquet checksum mismatch")

    table = pq.read_table(io.BytesIO(payload))

    if table.column_names != columns:
        raise ValueError("Gold Parquet columns differ")

    if table.num_rows != descriptor.get("row_count"):
        raise ValueError("Gold Parquet row count differs")

    for column, spec in exact_columns.items():
        expected = _arrow_type(spec)
        actual = table.schema.field(column).type

        if actual != expected:
            raise ValueError(
                "Gold physical Decimal type differs from contract"
            )

    return table.to_pandas()