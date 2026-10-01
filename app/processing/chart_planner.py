from dataclasses import dataclass


@dataclass(frozen=True)
class ChartPlan:
    chart_name: str
    title: str
    chart_type: str
    gold_artifact_id: int
    artifact_name: str
    dimension: str
    measure: str
    time_grain: str | None = None


PREFERRED_MEASURE_SOURCES = (
    "amount",
    "revenue",
    "sales",
    "profit",
    "quantity",
    "units",
)


def _looks_like_identifier(
    dimension: str,
) -> bool:
    """
    Avoid recommending charts for record-like identifiers
    such as order_id, transaction_id or event_id.
    """

    normalized = dimension.lower()

    return (
        normalized == "id"
        or normalized.endswith("_id")
    )


def _find_preferred_measure(
    mart: dict,
) -> str | None:
    """
    Choose one useful measure from the semantic catalog.

    Prefer SUM measures for additive business values.
    """

    columns = mart.get("columns", [])

    sum_measures = [
        column
        for column in columns
        if (
            column["column_role"] == "MEASURE"
            and column["aggregation_type"] == "SUM"
        )
    ]

    for preferred_source in PREFERRED_MEASURE_SOURCES:
        for column in sum_measures:
            source_column = (
                column.get("source_column") or ""
            ).lower()

            if preferred_source in source_column:
                return column["column_name"]

    if sum_measures:
        return sum_measures[0]["column_name"]

    metrics = mart.get("metrics", [])

    if metrics:
        return metrics[0]

    return None


def _build_title(
    dimension: str,
    measure: str,
    time_grain: str | None,
) -> str:
    readable_dimension = (
        dimension.replace("_", " ").title()
    )

    readable_measure = (
        measure
        .replace("_sum", "")
        .replace("_mean", "")
        .replace("_", " ")
        .title()
    )

    if time_grain:
        return (
            f"{readable_measure} by "
            f"{time_grain.title()}"
        )

    return (
        f"{readable_measure} by "
        f"{readable_dimension}"
    )


def discover_chart_plans(
    analytics_catalog: dict,
    max_charts: int = 6,
) -> list[ChartPlan]:
    """
    Discover safe, deterministic chart candidates
    from Gold semantic metadata.
    """

    if not analytics_catalog.get(
        "analytics_ready"
    ):
        return []

    plans = []

    marts = analytics_catalog.get(
        "marts",
        [],
    )

    # Prefer MONTH over YEAR over DAY for dashboard trends.
    time_priority = {
        "MONTH": 0,
        "YEAR": 1,
        "DAY": 2,
        None: 3,
    }

    ordered_marts = sorted(
        marts,
        key=lambda mart: time_priority.get(
            mart.get("time_grain"),
            4,
        ),
    )

    for mart in ordered_marts:

        dimensions = mart.get(
            "dimensions",
            [],
        )

        if len(dimensions) != 1:
            continue

        dimension = dimensions[0]

        if _looks_like_identifier(
            dimension
        ):
            continue

        measure = _find_preferred_measure(
            mart
        )

        if not measure:
            continue

        time_grain = mart.get(
            "time_grain"
        )

        chart_type = (
            "LINE"
            if time_grain
            else "BAR"
        )

        plans.append(
            ChartPlan(
                chart_name=(
                    f"{mart['artifact_name']}_"
                    f"{measure}_chart"
                ),
                title=_build_title(
                    dimension=dimension,
                    measure=measure,
                    time_grain=time_grain,
                ),
                chart_type=chart_type,
                gold_artifact_id=(
                    mart["gold_artifact_id"]
                ),
                artifact_name=(
                    mart["artifact_name"]
                ),
                dimension=dimension,
                measure=measure,
                time_grain=time_grain,
            )
        )

        if len(plans) >= max_charts:
            break

    return plans

def select_dashboard_charts(
    plans: list[ChartPlan],
    max_charts: int = 4,
) -> list[ChartPlan]:
    """
    Select a concise subset of discovered charts
    for the default analytics dashboard.

    Only one time-grain chart is selected for each
    time dimension to avoid showing redundant
    DAY/MONTH/YEAR views together.
    """

    selected = []
    selected_time_dimensions = set()

    time_priority = {
        "MONTH": 0,
        "YEAR": 1,
        "DAY": 2,
    }

    time_plans = sorted(
        [
            plan
            for plan in plans
            if plan.time_grain is not None
        ],
        key=lambda plan: time_priority.get(
            plan.time_grain,
            99,
        ),
    )

    for plan in time_plans:
        if plan.dimension in selected_time_dimensions:
            continue

        selected.append(plan)

        selected_time_dimensions.add(
            plan.dimension
        )

        if len(selected) >= max_charts:
            return selected

    categorical_plans = [
        plan
        for plan in plans
        if plan.time_grain is None
    ]

    for plan in categorical_plans:
        selected.append(plan)

        if len(selected) >= max_charts:
            break

    return selected