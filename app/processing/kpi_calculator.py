from typing import Any

import pandas as pd

from app.processing.kpi_planner import KPIPlan


def _normalize_value(value: Any):
    """
    Convert pandas/numpy scalar values into normal
    Python values suitable for JSON responses.
    """

    if pd.isna(value):
        return None

    if hasattr(value, "item"):
        return value.item()

    return value


def calculate_kpis(
    df: pd.DataFrame,
    plans: list[KPIPlan],
) -> list[dict]:
    """
    Calculate KPI values from a Gold base dataframe
    using previously validated KPI plans.
    """

    results = []

    for plan in plans:

        if plan.aggregation == "COUNT":
            value = len(df)

        else:
            if not plan.source_column:
                raise ValueError(
                    f"KPI '{plan.kpi_name}' requires "
                    "a source column"
                )

            if plan.source_column not in df.columns:
                raise ValueError(
                    f"Column '{plan.source_column}' "
                    "does not exist in Gold dataset"
                )

            series = df[plan.source_column]

            if plan.aggregation == "SUM":
                value = series.sum()

            elif plan.aggregation == "MEAN":
                value = series.mean()

            elif plan.aggregation == "MIN":
                value = series.min()

            elif plan.aggregation == "MAX":
                value = series.max()

            else:
                raise ValueError(
                    f"Unsupported KPI aggregation: "
                    f"{plan.aggregation}"
                )

        results.append(
            {
                "kpi_name": plan.kpi_name,
                "label": plan.label,
                "source_column": plan.source_column,
                "aggregation": plan.aggregation,
                "value": _normalize_value(value),
            }
        )

    return results