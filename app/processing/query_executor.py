from typing import Any

import pandas as pd

from app.schemas.analytics_query import AnalyticsQueryRequest


def _normalize_value(value: Any):
    """
    Convert pandas/numpy values into JSON-friendly
    Python values.
    """

    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if hasattr(value, "item"):
        return value.item()

    return value


def _apply_filter(
    df: pd.DataFrame,
    column: str,
    operator: str,
    value: Any,
) -> pd.DataFrame:
    """
    Apply one validated filter to a dataframe.
    """

    series = df[column]

    if operator == "EQ":
        return df[series == value]

    if operator == "NE":
        return df[series != value]

    if operator == "GT":
        return df[series > value]

    if operator == "GTE":
        return df[series >= value]

    if operator == "LT":
        return df[series < value]

    if operator == "LTE":
        return df[series <= value]

    if operator == "IN":
        return df[series.isin(value)]

    raise ValueError(
        f"Unsupported filter operator: {operator}"
    )


def execute_query(
    df: pd.DataFrame,
    query: AnalyticsQueryRequest,
) -> dict:
    """
    Execute a validated analytics query against
    one Gold artifact dataframe.
    """

    result_df = df.copy()

    # Apply filters
    for query_filter in query.filters:
        result_df = _apply_filter(
            df=result_df,
            column=query_filter.column,
            operator=query_filter.operator,
            value=query_filter.value,
        )

    # Apply sorting
    if query.sort:
        sort_columns = [
            item.column
            for item in query.sort
        ]

        ascending = [
            item.direction == "ASC"
            for item in query.sort
        ]

        result_df = result_df.sort_values(
            by=sort_columns,
            ascending=ascending,
        )

    # Apply selected columns
    if query.select:
        result_df = result_df[
            query.select
        ]

    # Apply row limit last
    result_df = result_df.head(
        query.limit
    )

    rows = []

    for record in result_df.to_dict(
        orient="records"
    ):
        rows.append(
            {
                key: _normalize_value(value)
                for key, value in record.items()
            }
        )

    return {
        "row_count": len(rows),
        "columns": list(result_df.columns),
        "rows": rows,
    }