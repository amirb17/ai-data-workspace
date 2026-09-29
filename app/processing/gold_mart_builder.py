import pandas as pd

from app.processing.gold_planner import GoldArtifactPlan


def _build_aggregations(
    df: pd.DataFrame,
    measures: list[str],
    aggregation_types: list[str],
) -> dict:
    """
    Build deterministic analytical aggregations for numeric
    measures.

    Each measure produces:
        SUM
        AVG
        MIN
        MAX
    """

    aggregations = {}

    for measure in measures:
        if measure not in df.columns:
            raise ValueError(
                f"Measure '{measure}' not found in dataframe"
            )

        aggregations[measure] = (
            aggregation_types.copy()
        )

    return aggregations


def build_mart(
    df: pd.DataFrame,
    plan: GoldArtifactPlan,
) -> pd.DataFrame:
    """
    Build one analytical MART from a Gold artifact plan.
    """

    if plan.artifact_type != "MART":
        raise ValueError(
            f"Unsupported artifact type: "
            f"{plan.artifact_type}"
        )

    if not plan.dimensions:
        raise ValueError(
            f"Mart '{plan.artifact_name}' "
            f"has no dimensions"
        )

    missing_dimensions = [
        column
        for column in plan.dimensions
        if column not in df.columns
    ]

    if missing_dimensions:
        raise ValueError(
            "Mart dimensions not found in dataframe: "
            f"{missing_dimensions}"
        )
    if not plan.measures:
        raise ValueError(
            f"Mart '{plan.artifact_name}' "
            f"has no measures"
        )

    if not plan.aggregations:
        raise ValueError(
            f"Mart '{plan.artifact_name}' "
            f"has no aggregations"
        )

    aggregations = _build_aggregations(
    df=df,
    measures=plan.measures,
    aggregation_types=plan.aggregations,
)

    mart = (
        df.groupby(
            plan.dimensions,
            dropna=False,
        )
        .agg(aggregations)
        .reset_index()
    )

    # Flatten pandas MultiIndex columns.
    mart.columns = [
        (
            column[0]
            if not column[1]
            else f"{column[0]}_{column[1]}"
        )
        if isinstance(column, tuple)
        else column
        for column in mart.columns
    ]

    # Number of source records contributing to each group.
    row_counts = (
        df.groupby(
            plan.dimensions,
            dropna=False,
        )
        .size()
        .reset_index(name="record_count")
    )

    mart = mart.merge(
        row_counts,
        on=plan.dimensions,
        how="left",
    )

    return mart