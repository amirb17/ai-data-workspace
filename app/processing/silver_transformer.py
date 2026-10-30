from datetime import date, datetime
import re

import pandas as pd
from decimal import Decimal

from app.processing.exact_decimal import (
    ExactDecimalSpec,
    parse_exact_decimal,
)

NULL_LIKE_VALUES = {
    "",
    "null",
    "none",
    "n/a",
    "na",
}


def normalize_nulls(series: pd.Series) -> pd.Series:
    """
    Convert common textual null representations into actual null values.
    """

    if not (
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
    ):
        return series

    def normalize(value):
        if pd.isna(value):
            return value

        if isinstance(value, str):
            cleaned = value.strip()

            if cleaned.lower() in NULL_LIKE_VALUES:
                return pd.NA

        return value

    return series.apply(normalize)


def trim_strings(series: pd.Series) -> pd.Series:
    """
    Remove leading/trailing whitespace from string values.
    """

    return series.apply(
        lambda value: value.strip()
        if isinstance(value, str)
        else value
    )

def convert_to_numeric(
    series: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """
    Convert values to numeric.

    Returns:
        converted_series
        invalid_mask
    """

    original_not_null = series.notna()

    converted = pd.to_numeric(
        series,
        errors="coerce",
    )

    invalid_mask = (
        original_not_null
        & converted.isna()
    )

    return converted, invalid_mask

def convert_to_datetime(
    series: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """
    Parse values as datetimes and identify values
    that could not be parsed.
    """

    original_not_null = series.notna()

    converted = pd.to_datetime(
        series,
        errors="coerce",
    )

    invalid_mask = (
        original_not_null
        & converted.isna()
    )

    return converted, invalid_mask

def convert_to_date(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Explicit calendar dates: no timestamp truncation or timezone inference."""
    def parse(value):
        if pd.isna(value):
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        return None

    converted = pd.Series([parse(value) for value in series], index=series.index, dtype=object)
    return converted, series.notna() & converted.isna()

def exact_decimal_spec(rule_config: dict) -> ExactDecimalSpec | None:
    """
    Legacy DECIMAL remains unchanged.

    Only a complete, explicitly opted-in contract may
    use exact decimal conversion.
    """

    exact_fields = {
        "mode",
        "precision",
        "scale",
    }

    if not exact_fields.intersection(rule_config):
        return None

    required = {
        "type",
        "mode",
        "precision",
        "scale",
    }

    if set(rule_config) != required:
        raise ValueError(
            "Incomplete exact decimal rule contract"
        )

    if (
        rule_config["type"] != "DECIMAL"
        or rule_config["mode"] != "EXACT"
    ):
        raise ValueError(
            "Unsupported exact numeric rule contract"
        )

    return ExactDecimalSpec(
        precision=rule_config["precision"],
        scale=rule_config["scale"],
    )


def convert_to_exact_decimal(
    series: pd.Series,
    spec: ExactDecimalSpec,
) -> tuple[pd.Series, pd.Series]:
    """
    Input must be original lexical source values, never
    values converted through pandas numeric inference.

    Malformed numeric rows are marked for quarantine.
    Invalid source representation fails the entire operation.
    """

    if any(
        not isinstance(value, str)
        for value in series.tolist()
    ):
        raise ValueError(
            "Exact decimal requires lexical source strings"
        )

    converted = []
    invalid = []

    for value in series.tolist():
        text = value.strip()

        if text.lower() in NULL_LIKE_VALUES:
            converted.append(None)
            invalid.append(False)
            continue

        try:
            number = parse_exact_decimal(
                text,
                spec,
            )

        except ValueError:
            converted.append(None)
            invalid.append(True)

        else:
            converted.append(number)
            invalid.append(False)

    return (
        pd.Series(
            converted,
            index=series.index,
            dtype=object,
        ),
        pd.Series(
            invalid,
            index=series.index,
            dtype=bool,
        ),
    )
def apply_transformations(
    df: pd.DataFrame,
    rules: list,
    lexical_frame: pd.DataFrame | None = None,
) -> tuple[
    pd.DataFrame,
    dict[str, pd.Series],
    dict[str, pd.Series],
]:
    df = df.copy()

    transformation_errors: dict[str, pd.Series] = {}
    original_null_masks: dict[str, pd.Series] = {}

    for rule in rules:
        column_name = rule[2]
        rule_type = rule[3]
        rule_config = rule[4]

        if column_name not in df.columns:
            continue

        if column_name not in original_null_masks:
            original_null_masks[column_name] = (
                df[column_name].isna().copy()
            )

        if rule_type == "NORMALIZE_NULL":
            df[column_name] = normalize_nulls(
                df[column_name]
            )

        elif rule_type == "TRIM_STRING":
            df[column_name] = trim_strings(
                df[column_name]
            )

        elif rule_type == "DATA_TYPE":
            target_type = rule_config.get("type")
            spec = exact_decimal_spec(rule_config)

            if spec is not None:
                if (
                    lexical_frame is None
                    or len(lexical_frame) != len(df)
                    or not lexical_frame.index.equals(df.index)
                    or column_name not in lexical_frame.columns
                    or not lexical_frame.columns.is_unique
                ):
                    raise ValueError(
                        "Exact decimal lexical source unavailable "
                        "or structurally inconsistent"
                    )

                converted, invalid_mask = (
                    convert_to_exact_decimal(
                        lexical_frame[column_name],
                        spec,
                    )
                )

                df[column_name] = converted

                transformation_errors[
                    f"{column_name}:INVALID_NUMERIC"
                ] = invalid_mask

            elif target_type in ("INTEGER", "DECIMAL"):
                # Existing V1 behavior must remain unchanged.
                converted, invalid_mask = convert_to_numeric(
                    df[column_name]
                )

                df[column_name] = converted

                transformation_errors[
                    f"{column_name}:INVALID_NUMERIC"
                ] = invalid_mask
            # if target_type in ("INTEGER", "DECIMAL"):
            #     converted, invalid_mask = convert_to_numeric(
            #         df[column_name]
            #     )

                # df[column_name] = converted

                # transformation_errors[
                #     f"{column_name}:INVALID_NUMERIC"
                # ] = invalid_mask

            elif target_type == "DATE":
                converted, invalid_mask = convert_to_date(df[column_name])
                df[column_name] = converted
                transformation_errors[f"{column_name}:INVALID_DATETIME"] = invalid_mask

            elif target_type == "DATETIME":
                converted, invalid_mask = convert_to_datetime(
                    df[column_name]
                )

                df[column_name] = converted

                transformation_errors[
                    f"{column_name}:INVALID_DATETIME"
                ] = invalid_mask

    return (
        df,
        transformation_errors,
        original_null_masks,
    )

def apply_common_cleaning(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Apply safe, generic Silver cleaning operations.

    - Trim leading/trailing whitespace from string values.
    - Normalize common textual null representations.
    """

    result = df.copy()

    for column_name in result.columns:

        if (
            pd.api.types.is_object_dtype(
                result[column_name]
            )
            or pd.api.types.is_string_dtype(
                result[column_name]
            )
        ):
            result[column_name] = trim_strings(
                result[column_name]
            )

            result[column_name] = normalize_nulls(
                result[column_name]
            )

    return result
