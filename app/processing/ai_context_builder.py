def build_ai_query_context(
    analytics_catalog: dict,
) -> dict:
    """
    Build a compact semantic description of the
    analytics data available to an AI query planner.

    Raw dataset rows and internal storage paths are
    intentionally excluded.
    """

    if not analytics_catalog.get(
        "analytics_ready"
    ):
        return {
            "analytics_ready": False,
            "artifacts": [],
        }

    artifacts = []

    for mart in analytics_catalog.get(
        "marts",
        [],
    ):
        artifacts.append(
            {
                "artifact_name":
                    mart["artifact_name"],
                "grain":
                    mart["grain"],
                "time_grain":
                    mart["time_grain"],
                "dimensions":
                    mart["dimensions"],
                "measures":
                    [
                        {
                            "column_name":
                                column["column_name"],
                            "source_column":
                                column["source_column"],
                            "aggregation":
                                column["aggregation_type"],
                        }
                        for column in mart.get(
                            "columns",
                            [],
                        )
                        if (
                            column["column_role"]
                            == "MEASURE"
                        )
                    ],
                "metrics":
                    [
                        {
                            "column_name":
                                column["column_name"],
                            "aggregation":
                                column[
                                    "aggregation_type"
                                ],
                        }
                        for column in mart.get(
                            "columns",
                            [],
                        )
                        if (
                            column["column_role"]
                            == "METRIC"
                        )
                    ],
            }
        )

    return {
        "analytics_ready": True,
        "dataset_version_id":
            analytics_catalog[
                "dataset_version_id"
            ],
        "artifacts": artifacts,
    }