"""Strict Parquet serialization for approved exact-decimal columns.

Pure artifact adapter. No DB writes, S3 writes, or state publication.
Legacy Parquet remains unchanged.
"""

import io

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from app.processing.exact_state_contract import (
    validate_exact_state_contract,
)


MAX_PARQUET_BYTES = 32 * 1024 * 1024
MAX_PARQUET_ROWS = 100_000
MAX_PARQUET_COLUMNS = 128


def _arrow_decimal_type(precision: int, scale: int):
    if precision <= 38:
        return pa.decimal128(precision, scale)

    return pa.decimal256(precision, scale)


def write_exact_parquet(
    frame: pd.DataFrame,
    approved_rules: list,
    business_columns: list[str],
) -> tuple[bytes, dict]:
    """Write typed Decimal columns and return the validated contract."""

    if (
        not frame.columns.is_unique
        or len(frame.columns) > MAX_PARQUET_COLUMNS
        or len(frame) > MAX_PARQUET_ROWS
    ):
        raise ValueError("Exact Parquet structure exceeds bounds")

    contract = validate_exact_state_contract(
        frame,
        approved_rules,
        business_columns,
    )

    exact = {
        column["name"]: column
        for column in contract["exact_columns"]
    }

    # Infer only non-exact columns using the existing pandas/Arrow
    # behavior. Never allow Arrow to infer an exact numeric type.
    ordinary = frame.drop(columns=list(exact))

    ordinary_table = pa.Table.from_pandas(
        ordinary,
        preserve_index=False,
        safe=True,
    )

    ordinary_arrays = {
        name: ordinary_table[name]
        for name in ordinary_table.column_names
    }

    arrays = []

    for name in frame.columns:
        if name in exact:
            specification = exact[name]

            arrow_type = _arrow_decimal_type(
                specification["precision"],
                specification["scale"],
            )

            arrays.append(
                pa.array(
                    frame[name].tolist(),
                    type=arrow_type,
                    safe=True,
                )
            )
        else:
            arrays.append(ordinary_arrays[name])

    table = pa.Table.from_arrays(
        arrays,
        names=list(frame.columns),
    )

    buffer = io.BytesIO()

    pq.write_table(
        table,
        buffer,
        compression="snappy",
    )

    payload = buffer.getvalue()

    if len(payload) > MAX_PARQUET_BYTES:
        raise ValueError("Exact Parquet byte bound exceeded")

    # Verify readability and the exact physical schema.
    restored = read_exact_parquet(
        payload,
        contract,
        approved_rules,
        business_columns,
        expected_columns=list(frame.columns),
    )

    try:
        pd.testing.assert_frame_equal(
            frame.reset_index(drop=True),
            restored.reset_index(drop=True),
            check_dtype=False,
            check_exact=True,
        )
    except AssertionError:
        raise ValueError(
            "Exact Parquet round-trip differs from input"
        ) from None

    return payload, contract


def read_exact_parquet(
    payload: bytes,
    pinned_contract: dict,
    approved_rules: list,
    business_columns: list[str],
    *,
    expected_columns: list[str],
) -> pd.DataFrame:
    """Validate the physical schema before accepting decoded values."""

    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > MAX_PARQUET_BYTES
    ):
        raise ValueError("Invalid exact Parquet artifact")

    stream = io.BytesIO(payload)

    parquet = pq.ParquetFile(stream)

    if (
        parquet.metadata.num_rows > MAX_PARQUET_ROWS
        or len(parquet.schema_arrow.names) > MAX_PARQUET_COLUMNS
        or parquet.schema_arrow.names != expected_columns
    ):
        raise ValueError("Exact Parquet structure differs")

    exact = pinned_contract["exact_columns"]

    for column in exact:
        name = column["name"]

        expected_type = _arrow_decimal_type(
            column["precision"],
            column["scale"],
        )

        if parquet.schema_arrow.field(name).type != expected_type:
            raise ValueError(
                "Physical exact decimal type differs from contract"
            )

    restored = parquet.read().to_pandas()

    actual_contract = validate_exact_state_contract(
        restored,
        approved_rules,
        business_columns,
    )

    if actual_contract != pinned_contract:
        raise ValueError(
            "Exact Parquet contract differs from pinned evidence"
        )

    return restored