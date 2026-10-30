import pandas as pd

from app.processing.gold_planner import GoldArtifactPlan
from decimal import Decimal
from fractions import Fraction

def _decimal_values(series: pd.Series) -> list[Decimal]:
    """Validate an exact Decimal measure without float conversion."""
    values = []

    for value in series:
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError(
                    "Non-finite Decimal measure"
                )
            values.append(value)

        elif not pd.isna(value):
            raise ValueError(
                "Mixed numeric types in Exact Decimal measure"
            )

    return values


def _fraction_as_decimal(value: Fraction) -> Decimal:
    """
    Convert an exact rational number to Decimal only
    when it has a terminating decimal expansion.

    No floating-point or implicit rounding.
    """
    denominator = value.denominator
    twos = 0
    fives = 0

    while denominator % 2 == 0:
        denominator //= 2
        twos += 1

    while denominator % 5 == 0:
        denominator //= 5
        fives += 1

    if denominator != 1:
        raise ValueError(
            "Exact Decimal average requires an explicit "
            "rounding policy for non-terminating results"
        )

    scale = max(twos, fives)

    scaled = value.numerator * (
        10 ** scale // value.denominator
    )

    digits = tuple(
        int(digit)
        for digit in str(abs(scaled))
    )

    # The tuple constructor does not round through the
    # active decimal context.
    return Decimal(
        (int(scaled < 0), digits, -scale)
    )


def _exact_decimal_sum(series: pd.Series) -> Decimal:
    values = _decimal_values(series)

    total = sum(
        (Fraction(value) for value in values),
        Fraction(0, 1),
    )

    return _fraction_as_decimal(total)


def _exact_decimal_mean(series: pd.Series):
    values = _decimal_values(series)

    if not values:
        return None

    total = sum(
        (Fraction(value) for value in values),
        Fraction(0, 1),
    )

    return _fraction_as_decimal(
        total / len(values)
    )


# Retain the same aggregation column names expected
# by the Gold semantic catalog.
_exact_decimal_sum.__name__ = "sum"
_exact_decimal_mean.__name__ = "mean"

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

        is_exact = any(
            isinstance(value, Decimal)
            for value in df[measure]
        )

        if is_exact:
            # Reject mixed/non-finite values before aggregation.
            _decimal_values(df[measure])

            exact_functions = {
                "sum": _exact_decimal_sum,
                "mean": _exact_decimal_mean,
            }

            aggregations[measure] = [
                exact_functions.get(aggregation, aggregation)
                for aggregation in aggregation_types
            ]

        else:
            # Legacy V1 aggregations remain unchanged.
            aggregations[measure] = aggregation_types.copy()

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
    working_df, grouping_dimensions = (
    _apply_time_grain(
        df=df,
        plan=plan,
    )
)
    aggregations = _build_aggregations(
    df=working_df,
    measures=plan.measures,
    aggregation_types=plan.aggregations,
)

    mart = (
        working_df.groupby(
            grouping_dimensions,
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
        working_df.groupby(
            grouping_dimensions,
            dropna=False,
        )
        .size()
        .reset_index(name="record_count")
    )

    mart = mart.merge(
        row_counts,
        on=grouping_dimensions,
        how="left",
    )

    return mart

def _apply_time_grain(
    df: pd.DataFrame,
    plan: GoldArtifactPlan,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Prepare grouping dimensions for a time-grained Gold mart.

    The input dataframe is never modified in place.
    """

    working_df = df.copy()

    if plan.time_grain is None:
        return working_df, plan.dimensions.copy()

    if len(plan.dimensions) != 1:
        raise ValueError(
            f"Time-grained mart '{plan.artifact_name}' "
            "must currently contain exactly one time dimension"
        )

    source_column = plan.dimensions[0]

    if source_column not in working_df.columns:
        raise ValueError(
            f"Time dimension '{source_column}' "
            "not found in dataframe"
        )

    parsed_time = pd.to_datetime(
        working_df[source_column],
        errors="coerce",
    )

    if (
        working_df[source_column].notna()
        & parsed_time.isna()
    ).any():
        raise ValueError(
            f"Time dimension '{source_column}' contains "
            "values that cannot be converted to datetime"
        )

    grain = plan.time_grain.upper()

    if grain == "DAY":
        working_df[source_column] = (
            parsed_time.dt.floor("D")
        )

    elif grain == "MONTH":
        working_df[source_column] = (
            parsed_time.dt.to_period("M")
            .dt.to_timestamp()
        )

    elif grain == "YEAR":
        working_df[source_column] = (
            parsed_time.dt.to_period("Y")
            .dt.to_timestamp()
        )

    else:
        raise ValueError(
            f"Unsupported time grain: {plan.time_grain}"
        )

    return (
        working_df,
        plan.dimensions.copy(),
    )