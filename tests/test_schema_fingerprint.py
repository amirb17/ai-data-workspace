import pandas as pd

from app.processing.schema_fingerprint import (
    calculate_schema_hash,
    normalize_dtype,
)


def test_column_order_does_not_affect_hash():
    df1 = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    df2 = pd.DataFrame({"b": ["x", "y"], "a": [1, 2]})

    assert calculate_schema_hash(df1) == calculate_schema_hash(df2)


def test_column_name_case_and_whitespace_do_not_affect_hash():
    df1 = pd.DataFrame({"Amount": [1.0], "City ": ["x"]})
    df2 = pd.DataFrame({" amount": [2.5], "city": ["y"]})

    assert calculate_schema_hash(df1) == calculate_schema_hash(df2)


def test_changed_column_type_changes_hash():
    # int -> INTEGER, float -> DECIMAL: distinct normalized types.
    df1 = pd.DataFrame({"amount": [1, 2]})
    df2 = pd.DataFrame({"amount": [1.5, 2.5]})

    assert calculate_schema_hash(df1) != calculate_schema_hash(df2)


def test_equivalent_int_dtypes_produce_same_hash():
    # Both normalize to INTEGER regardless of bit width.
    df1 = pd.DataFrame({"amount": pd.array([1, 2], dtype="int32")})
    df2 = pd.DataFrame({"amount": pd.array([1, 2], dtype="int64")})

    assert calculate_schema_hash(df1) == calculate_schema_hash(df2)


def test_added_column_changes_hash():
    df1 = pd.DataFrame({"amount": [1]})
    df2 = pd.DataFrame({"amount": [1], "city": ["x"]})

    assert calculate_schema_hash(df1) != calculate_schema_hash(df2)


def test_normalize_dtype_mapping():
    assert normalize_dtype("int64") == "INTEGER"
    assert normalize_dtype("float64") == "DECIMAL"
    assert normalize_dtype("bool") == "BOOLEAN"
    assert normalize_dtype("datetime64[ns]") == "DATETIME"
    assert normalize_dtype("object") == "TEXT"
