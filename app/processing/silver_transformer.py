import pandas as pd


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

def apply_transformations(
    df: pd.DataFrame,
    rules: list,
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

            if target_type in ("INTEGER", "DECIMAL"):
                converted, invalid_mask = convert_to_numeric(
                    df[column_name]
                )

                df[column_name] = converted

                transformation_errors[
                    f"{column_name}:INVALID_NUMERIC"
                ] = invalid_mask

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