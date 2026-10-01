from app.processing.chart_planner import (
    discover_chart_plans,
    select_dashboard_charts,
)


def _humanize(name: str) -> str:
    """
    Convert technical column names into readable text.
    """

    return (
        name
        .replace("_sum", "")
        .replace("_mean", "")
        .replace("_min", "")
        .replace("_max", "")
        .replace("_", " ")
        .strip()
        .lower()
    )


def _aggregation_label(measure: str) -> str:
    """
    Infer a human-readable aggregation label from a
    semantic measure name.
    """

    if measure.endswith("_sum"):
        return "total"

    if measure.endswith("_mean"):
        return "average"

    if measure.endswith("_min"):
        return "minimum"

    if measure.endswith("_max"):
        return "maximum"

    return ""


def generate_question_suggestions(
    analytics_catalog: dict,
    max_questions: int = 6,
) -> list[dict]:
    """
    Generate deterministic, answerable questions from
    Gold semantic metadata.

    These are not AI-generated questions.
    """

    if not analytics_catalog.get(
        "analytics_ready"
    ):
        return []

    discovered_plans = discover_chart_plans(
        analytics_catalog=analytics_catalog,
        max_charts=20,
    )

    plans = select_dashboard_charts(
        plans=discovered_plans,
        max_charts=max_questions,
    )

    suggestions = []

    for plan in plans:
        dimension = _humanize(
            plan.dimension
        )

        measure = _humanize(
            plan.measure
        )

        aggregation = _aggregation_label(
            plan.measure
        )

        if aggregation:
            measure_phrase = (
                f"{aggregation} {measure}"
            )
        else:
            measure_phrase = measure

        if plan.time_grain:
            grain = plan.time_grain.lower()

            question = (
                f"Show {measure_phrase} "
                f"trend by {grain}"
            )

            question_type = "TREND"
            structured_query = {
        "artifact_name": plan.artifact_name,
        "select": [
            plan.dimension,
            plan.measure,
        ],
        "filters": [],
        "sort": [
            {
                "column": plan.dimension,
                "direction": "ASC",
            }
        ],
        "limit": 1000,
    }

        else:
            question = (
                f"Which {dimension} has the "
                f"highest {measure_phrase}?"
            )

            question_type = "RANKING"
            structured_query = {
            "artifact_name": plan.artifact_name,
            "select": [
                plan.dimension,
                plan.measure,
            ],
            "filters": [],
            "sort": [
                {
                    "column": plan.measure,
                    "direction": "DESC",
                }
            ],
            "limit": 1,
        }

        suggestions.append(
            {
                "question": question,
                "question_type":
                    question_type,
                "artifact_name":
                    plan.artifact_name,
                "gold_artifact_id":
                    plan.gold_artifact_id,
                "dimension":
                    plan.dimension,
                "measure":
                    plan.measure,
                "time_grain":
                    plan.time_grain,
                "structured_query":
                    structured_query,
            }
        )

        if len(suggestions) >= max_questions:
            break

    return suggestions