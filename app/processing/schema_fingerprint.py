import hashlib

import pandas as pd


def normalize_dtype(dtype: object) -> str:
    """
    Convert Pandas dtypes into stable logical data types.

    The goal is to avoid generating different schema hashes
    for equivalent representations.
    """
    dtype_str = str(dtype).lower()

    if "int" in dtype_str:
        return "INTEGER"

    if "float" in dtype_str:
        return "DECIMAL"

    if "bool" in dtype_str:
        return "BOOLEAN"

    if "datetime" in dtype_str:
        return "DATETIME"

    if "date" in dtype_str:
        return "DATETIME"

    return "TEXT"


def normalize_column_name(column_name: object) -> str:
    """
    Normalize a column name for schema comparison.
    """
    return str(column_name).strip().lower()


def build_canonical_schema(df: pd.DataFrame) -> str:
    """
    Build a deterministic representation of the DataFrame schema.

    Column order is ignored because the logical schema is based
    on column names and types rather than CSV column position.
    """
    columns = []

    for column_name, dtype in df.dtypes.items():
        normalized_name = normalize_column_name(column_name)
        normalized_type = normalize_dtype(dtype)

        columns.append(
            (normalized_name, normalized_type)
        )

    columns.sort(key=lambda item: item[0])

    return "|".join(
        f"{column_name}:{data_type}"
        for column_name, data_type in columns
    )


def calculate_schema_hash(df: pd.DataFrame) -> str:
    """
    Calculate a SHA-256 fingerprint for the logical schema.
    """
    canonical_schema = build_canonical_schema(df)

    return hashlib.sha256(
        canonical_schema.encode("utf-8")
    ).hexdigest()