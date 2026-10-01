from app.schemas.analytics_query import AnalyticsQueryRequest


def validate_query_against_catalog(
    query: AnalyticsQueryRequest,
    analytics_catalog: dict,
) -> dict:
    """
    Validate a structured analytics query against
    the dataset's semantic catalog.

    Returns the matched artifact if valid.
    """

    if not analytics_catalog.get("analytics_ready"):
        raise ValueError(
            "Dataset is not analytics-ready"
        )

    artifact = None

    for mart in analytics_catalog.get("marts", []):
        if mart["artifact_name"] == query.artifact_name:
            artifact = mart
            break

    if artifact is None:
        raise ValueError(
            f"Unknown analytics artifact: "
            f"{query.artifact_name}"
        )

    available_columns = {
        column["column_name"]
        for column in artifact.get("columns", [])
    }

    if not available_columns:
        raise ValueError(
            f"Artifact '{query.artifact_name}' "
            "does not have semantic columns"
        )

    selected_columns = (
        query.select
        if query.select
        else list(available_columns)
    )

    for column in selected_columns:
        if column not in available_columns:
            raise ValueError(
                f"Unknown selected column "
                f"'{column}' for artifact "
                f"'{query.artifact_name}'"
            )

    for query_filter in query.filters:
        if query_filter.column not in available_columns:
            raise ValueError(
                f"Unknown filter column "
                f"'{query_filter.column}'"
            )

        if (
            query_filter.operator == "IN"
            and not isinstance(
                query_filter.value,
                list,
            )
        ):
            raise ValueError(
                "IN filter requires a list value"
            )

    for query_sort in query.sort:
        if query_sort.column not in available_columns:
            raise ValueError(
                f"Unknown sort column "
                f"'{query_sort.column}'"
            )

    return artifact