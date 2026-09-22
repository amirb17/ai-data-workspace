import pandas as pd
from pandas.api.types import is_numeric_dtype


def profile_dataframe(df: pd.DataFrame) -> list[dict]:
    profiles = []

    for column_name in df.columns:
        series = df[column_name]

        null_count = int(series.isna().sum())
        distinct_count = int(series.nunique(dropna=True))

        non_null_count = int(series.notna().sum())

        duplicate_count = max(
            non_null_count - distinct_count,
            0,
        )

        min_value = None
        max_value = None
        negative_count = None

        non_null_series = series.dropna()

        if not non_null_series.empty:
            try:
                min_value = str(non_null_series.min())
                max_value = str(non_null_series.max())
            except (TypeError, ValueError):
                pass

        if is_numeric_dtype(series):
            negative_count = int((series < 0).sum())

        profiles.append(
            {
                "column_name": column_name,
                "inferred_type": str(series.dtype),
                "null_count": null_count,
                "distinct_count": distinct_count,
                "duplicate_count": duplicate_count,
                "min_value": min_value,
                "max_value": max_value,
                "negative_count": negative_count,
            }
        )

    return profiles