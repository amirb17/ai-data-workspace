from typing import Any

import pandas as pd

from app.processing.chart_planner import ChartPlan


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


def build_chart_data(
    df: pd.DataFrame,
    plan: ChartPlan,
) -> dict:
    """
    Build frontend-ready chart data from a Gold mart
    using a validated ChartPlan.
    """

    required_columns = [
        plan.dimension,
        plan.measure,
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            "Gold mart is missing required chart "
            f"columns: {missing_columns}"
        )

    chart_df = df[
        required_columns
    ].copy()

    if plan.chart_type == "LINE":
        chart_df = chart_df.sort_values(
            by=plan.dimension,
        )

    elif plan.chart_type == "BAR":
        chart_df = chart_df.sort_values(
            by=plan.measure,
            ascending=False,
        )

    data = []

    for record in chart_df.to_dict(
        orient="records"
    ):
        data.append(
            {
                key: _normalize_value(value)
                for key, value in record.items()
            }
        )

    return {
        "chart_name": plan.chart_name,
        "title": plan.title,
        "chart_type": plan.chart_type,
        "gold_artifact_id":
            plan.gold_artifact_id,
        "artifact_name":
            plan.artifact_name,
        "dimension": plan.dimension,
        "measure": plan.measure,
        "time_grain": plan.time_grain,
        "data": data,
    }