from dataclasses import asdict, dataclass


NUMERIC_TYPES = {
    "int8",
    "int16",
    "int32",
    "int64",
    "float16",
    "float32",
    "float64",
    "decimal",
}

DATETIME_TYPES = {
    "datetime",
    "datetime64",
    "datetime64[ns]",
    "date",
    "timestamp",
}

IDENTIFIER_HINTS = (
    "_id",
    "id",
)

TIME_NAME_HINTS = (
    "date",
    "time",
    "timestamp",
    "created_at",
    "updated_at",
)
DEFAULT_MEASURE_AGGREGATIONS = (
    "sum",
    "mean",
    "min",
    "max",
)


@dataclass(frozen=True)
class GoldArtifactPlan:
    artifact_type: str
    artifact_name: str
    dimensions: list[str]
    measures: list[str]
    aggregations: list[str]
    reason: str
    time_grain: str | None = None

@dataclass(frozen=True)
class GoldPlan:
    base_required: bool
    dimensions: list[str]
    aggregation_dimensions: list[str]
    measures: list[str]
    time_dimensions: list[str]
    artifacts: list[GoldArtifactPlan]

    def to_dict(self) -> dict:
        return asdict(self)


def _is_identifier(column_name: str) -> bool:
    normalized = column_name.lower()

    return (
        normalized == "id"
        or normalized.endswith("_id")
    )


def _is_time_column(
    column_name: str,
    inferred_type: str,
) -> bool:
    normalized_name = column_name.lower()
    normalized_type = inferred_type.lower()

    if normalized_type in DATETIME_TYPES:
        return True

    return any(
        hint in normalized_name
        for hint in TIME_NAME_HINTS
    )


def build_gold_plan(
    profiles: list[tuple],
) -> GoldPlan:
    """
    Build a deterministic analytical Gold plan from dataset
    profiling metadata.

    Expected profile tuple:
        (
            profile_id,
            file_id,
            column_name,
            inferred_type,
            null_count,
            distinct_count,
            duplicate_count,
            min_value,
            max_value,
            negative_count,
        )
    """

    dimensions: list[str] = []
    measures: list[str] = []
    time_dimensions: list[str] = []
    identifier_dimensions: list[str] = []

    aggregation_dimensions: list[str] = []

    total_rows = 0

    if profiles:
        # We do not currently store total_rows inside each profile,
        # so estimate dataset cardinality using the highest observed
        # distinct count.
        total_rows = max(
            profile[5] or 0
            for profile in profiles
        )

    for profile in profiles:
        column_name = profile[2]
        inferred_type = profile[3]
        distinct_count = profile[5] or 0

        if _is_time_column(
            column_name,
            inferred_type,
        ):
            time_dimensions.append(column_name)
            continue

        if _is_identifier(column_name):
            identifier_dimensions.append(column_name)
            dimensions.append(column_name)
            continue

        if inferred_type.lower() in NUMERIC_TYPES:
            measures.append(column_name)
            continue

        dimensions.append(column_name)

        # Low/medium-cardinality categorical fields are useful
        # aggregation dimensions.
        if total_rows > 0:
            cardinality_ratio = (
                distinct_count / total_rows
            )

            if cardinality_ratio <= 0.80:
                aggregation_dimensions.append(
                    column_name
                )

    artifacts: list[GoldArtifactPlan] = []

    # ---------------------------------------------------------
    # Entity/customer-style aggregate
    # ---------------------------------------------------------
    if identifier_dimensions and measures:
        primary_identifier = identifier_dimensions[0]

        artifacts.append(
            GoldArtifactPlan(
                artifact_type="MART",
                artifact_name=(
                    f"{primary_identifier}_summary"
                ),
                dimensions=[primary_identifier],
                measures=measures.copy(),
                aggregations=list(
                    DEFAULT_MEASURE_AGGREGATIONS
                ),
                reason=(
                    "Identifier and numeric measures are "
                    "available for entity-level aggregation."
                ),
            )
)

    # ---------------------------------------------------------
    # Categorical aggregate
    # ---------------------------------------------------------
    for dimension in aggregation_dimensions:
        if measures:
            artifacts.append(
                GoldArtifactPlan(
                    artifact_type="MART",
                    artifact_name=f"{dimension}_summary",
                    dimensions=[dimension],
                    measures=measures.copy(),
                    aggregations=list(
                        DEFAULT_MEASURE_AGGREGATIONS
                    ),
                    reason=(
                        "Categorical dimension has suitable "
                        "cardinality for analytical aggregation."
                    ),
                )
            )

    # ---------------------------------------------------------
    # Time aggregate
    # ---------------------------------------------------------
# ---------------------------------------------------------
# Time-based analytical aggregates
# ---------------------------------------------------------
    if time_dimensions and measures:
        primary_time_dimension = time_dimensions[0]

        for time_grain in (
            "DAY",
            "MONTH",
            "YEAR",
        ):
            artifact_name = (
                f"{primary_time_dimension}_"
                f"{time_grain.lower()}_summary"
            )

            artifacts.append(
                GoldArtifactPlan(
                    artifact_type="MART",
                    artifact_name=artifact_name,
                    dimensions=[
                        primary_time_dimension
                    ],
                    measures=measures.copy(),
                    aggregations=[
                        "sum",
                        "mean",
                        "min",
                        "max",
                    ],
                    reason=(
                        "Datetime dimension and numeric "
                        f"measures support {time_grain.lower()}-"
                        "level analytical aggregation."
                    ),
                    time_grain=time_grain,
                )
            )

    return GoldPlan(
    base_required=True,
    dimensions=dimensions,
    aggregation_dimensions=aggregation_dimensions,
    measures=measures,
    time_dimensions=time_dimensions,
    artifacts=artifacts,
)