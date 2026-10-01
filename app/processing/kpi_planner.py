from dataclasses import dataclass


@dataclass(frozen=True)
class KPIPlan:
    kpi_name: str
    source_column: str | None
    aggregation: str
    label: str


AVERAGE_PREFERRED_TERMS = (
    "price",
    "rate",
    "ratio",
    "percentage",
    "percent",
    "margin",
    "score",
)

SUM_PREFERRED_TERMS = (
    "amount",
    "revenue",
    "sales",
    "quantity",
    "qty",
    "units",
    "profit",
    "cost",
    "discount",
)


def _humanize_column_name(column_name: str) -> str:
    return column_name.replace("_", " ").strip().title()


def _preferred_aggregation(column_name: str) -> str:
    """
    Choose a conservative default aggregation for a measure.

    This is heuristic-based and can later be replaced/enriched
    by stronger semantic metadata.
    """

    normalized = column_name.lower()

    if any(
        term in normalized
        for term in AVERAGE_PREFERRED_TERMS
    ):
        return "MEAN"

    if any(
        term in normalized
        for term in SUM_PREFERRED_TERMS
    ):
        return "SUM"

    return "MEAN"


def _build_label(
    source_column: str,
    aggregation: str,
) -> str:
    readable_name = _humanize_column_name(
        source_column
    )

    if aggregation == "SUM":
        return f"Total {readable_name}"

    if aggregation == "MEAN":
        return f"Average {readable_name}"

    return readable_name


def discover_kpi_plans(
    analytics_catalog: dict,
    max_kpis: int = 6,
) -> list[KPIPlan]:
    """
    Discover deterministic KPI candidates from the Gold
    semantic catalog.

    The planner does not calculate values. It only decides
    what should be calculated.
    """

    if not analytics_catalog.get("analytics_ready"):
        return []

    plans = [
        KPIPlan(
            kpi_name="record_count",
            source_column=None,
            aggregation="COUNT",
            label="Total Records",
        )
    ]

    discovered_measures = {}

    for mart in analytics_catalog.get("marts", []):
        for column in mart.get("columns", []):
            if column["column_role"] != "MEASURE":
                continue

            source_column = column.get(
                "source_column"
            )

            if not source_column:
                continue

            discovered_measures.setdefault(
                source_column,
                set(),
            ).add(
                column.get("aggregation_type")
            )

    for source_column, available_aggregations in (
        discovered_measures.items()
    ):
        preferred_aggregation = (
            _preferred_aggregation(source_column)
        )

        if (
            preferred_aggregation
            not in available_aggregations
        ):
            continue

        plans.append(
            KPIPlan(
                kpi_name=(
                    f"{source_column}_"
                    f"{preferred_aggregation.lower()}"
                ),
                source_column=source_column,
                aggregation=preferred_aggregation,
                label=_build_label(
                    source_column,
                    preferred_aggregation,
                ),
            )
        )

        if len(plans) >= max_kpis:
            break

    return plans