from app.db.analytics_repository import (
    get_gold_artifacts_for_dataset_version,
    get_gold_artifact_semantic_columns_for_artifacts,
)
from app.processing.gold_artifact_reader import (
    read_gold_artifact,
)
from app.processing.kpi_calculator import (
    calculate_kpis,
)
from app.processing.kpi_planner import (
    discover_kpi_plans,
)
from app.processing.chart_data_builder import (
    build_chart_data,
)
from app.processing.chart_planner import (
    discover_chart_plans,
    select_dashboard_charts,
)
from app.services.question_suggestion_service import (
    generate_question_suggestions,
)
from app.processing.query_executor import (
    execute_query,
)
from app.processing.query_validator import (
    validate_query_against_catalog,
)
from app.schemas.analytics_query import (
    AnalyticsQueryRequest,
)
from app.ai.query_planner import (
    plan_analytics_query,
)
from app.processing.ai_context_builder import (
    build_ai_query_context,
)
from app.ai.answer_generator import (
    generate_friendly_answer,
)

def get_dataset_analytics_catalog(
    dataset_version_id: int,
) -> dict:
    """
    Build a clean analytics catalog for a dataset version.

    This is the service-layer representation used by future
    dashboard, KPI, query and AI analytics features.
    """

    rows = get_gold_artifacts_for_dataset_version(
        dataset_version_id
    )

    artifacts = []

    for row in rows:
        artifacts.append(
            {
                "gold_artifact_id": row[0],
                "gold_run_id": row[1],
                "artifact_type": row[2],
                "artifact_name": row[3],
                "storage_path": row[4],
                "row_count": row[5],
                "grain": row[6],
                "time_grain": row[7],
                "created_at": row[8],
            }
        )
        artifact_ids = [
        artifact["gold_artifact_id"]
        for artifact in artifacts
    ]

    semantic_rows = (
        get_gold_artifact_semantic_columns_for_artifacts(
            artifact_ids
        )
    )

    semantic_columns_by_artifact = {}

    for row in semantic_rows:
        gold_artifact_id = row[1]

        column = {
            "column_name": row[2],
            "column_role": row[3],
            "source_column": row[4],
            "aggregation_type": row[5],
            "ordinal_position": row[6],
            "data_type": row[7],
        }

        semantic_columns_by_artifact.setdefault(
            gold_artifact_id,
            [],
        ).append(column)
        for artifact in artifacts:
            semantic_columns = (
                semantic_columns_by_artifact.get(
                    artifact["gold_artifact_id"],
                    [],
                )
            )

            artifact["columns"] = semantic_columns

            artifact["dimensions"] = [
                column["column_name"]
                for column in semantic_columns
                if column["column_role"] == "DIMENSION"
            ]

            artifact["measures"] = [
                column["column_name"]
                for column in semantic_columns
                if column["column_role"] == "MEASURE"
            ]

            artifact["metrics"] = [
                column["column_name"]
                for column in semantic_columns
                if column["column_role"] == "METRIC"
            ]
    base_artifact = next(
        (
            artifact
            for artifact in artifacts
            if artifact["artifact_type"] == "BASE"
        ),
        None,
    )

    marts = [
        artifact
        for artifact in artifacts
        if artifact["artifact_type"] == "MART"
    ]

    return {
        "dataset_version_id": dataset_version_id,
        "analytics_ready": base_artifact is not None,
        "base_artifact": base_artifact,
        "mart_count": len(marts),
        "marts": marts,
    }

def get_dataset_kpis(
    dataset_version_id: int,
    max_kpis: int = 6,
) -> dict:
    """
    Return dashboard-ready KPIs for an analytics-ready
    dataset version.
    """

    catalog = get_dataset_analytics_catalog(
        dataset_version_id
    )

    if not catalog["analytics_ready"]:
        return {
            "dataset_version_id": dataset_version_id,
            "analytics_ready": False,
            "kpis": [],
        }

    base_artifact = catalog.get("base_artifact")

    if not base_artifact:
        raise ValueError(
            f"Dataset version {dataset_version_id} "
            "does not have a Gold base artifact"
        )

    plans = discover_kpi_plans(
        analytics_catalog=catalog,
        max_kpis=max_kpis,
    )

    gold_df = read_gold_artifact(
        base_artifact["storage_path"]
    )

    kpis = calculate_kpis(
        df=gold_df,
        plans=plans,
    )

    return {
        "dataset_version_id": dataset_version_id,
        "analytics_ready": True,
        "source_artifact": {
            "gold_artifact_id":
                base_artifact["gold_artifact_id"],
            "artifact_name":
                base_artifact["artifact_name"],
        },
        "kpi_count": len(kpis),
        "kpis": kpis,
    }

def get_dataset_analytics_overview(
    dataset_version_id: int,
) -> dict:
    """
    Return frontend-safe analytics metadata for a
    processed dataset version.

    Internal storage paths are intentionally excluded.
    """

    catalog = get_dataset_analytics_catalog(
        dataset_version_id
    )

    if not catalog["analytics_ready"]:
        return {
            "dataset_version_id": dataset_version_id,
            "analytics_ready": False,
            "base_artifact": None,
            "mart_count": 0,
            "marts": [],
        }

    base = catalog["base_artifact"]

    public_base = {
        "gold_artifact_id": base["gold_artifact_id"],
        "artifact_name": base["artifact_name"],
        "artifact_type": base["artifact_type"],
        "row_count": base["row_count"],
    }

    public_marts = []

    for mart in catalog["marts"]:
        public_marts.append(
            {
                "gold_artifact_id":
                    mart["gold_artifact_id"],
                "artifact_name":
                    mart["artifact_name"],
                "artifact_type":
                    mart["artifact_type"],
                "row_count":
                    mart["row_count"],
                "grain":
                    mart["grain"],
                "time_grain":
                    mart["time_grain"],
                "dimensions":
                    mart["dimensions"],
                "measures":
                    mart["measures"],
                "metrics":
                    mart["metrics"],
                "columns":
                    mart["columns"],
            }
        )

    return {
        "dataset_version_id": dataset_version_id,
        "analytics_ready": True,
        "base_artifact": public_base,
        "mart_count": len(public_marts),
        "marts": public_marts,
    }

def _find_artifact(
    catalog: dict,
    gold_artifact_id: int,
) -> dict | None:
    """
    Find one Gold artifact inside an internal
    analytics catalog.
    """

    base_artifact = catalog.get(
        "base_artifact"
    )

    if (
        base_artifact
        and base_artifact["gold_artifact_id"]
        == gold_artifact_id
    ):
        return base_artifact

    for mart in catalog.get("marts", []):
        if (
            mart["gold_artifact_id"]
            == gold_artifact_id
        ):
            return mart

    return None

def get_dataset_charts(
    dataset_version_id: int,
    max_charts: int = 6,
) -> dict:
    """
    Discover and build dashboard-ready charts for
    an analytics-ready dataset version.
    """

    catalog = get_dataset_analytics_catalog(
        dataset_version_id
    )

    if not catalog["analytics_ready"]:
        return {
            "dataset_version_id":
                dataset_version_id,
            "analytics_ready": False,
            "chart_count": 0,
            "charts": [],
        }

    all_plans = discover_chart_plans(
    analytics_catalog=catalog,
    max_charts=20,
)

    plans = select_dashboard_charts(
        plans=all_plans,
        max_charts=max_charts,
    )

    charts = []

    for plan in plans:

        artifact = _find_artifact(
            catalog=catalog,
            gold_artifact_id=(
                plan.gold_artifact_id
            ),
        )

        if not artifact:
            raise ValueError(
                "Gold artifact "
                f"{plan.gold_artifact_id} "
                "could not be found"
            )

        df = read_gold_artifact(
            artifact["storage_path"]
        )

        chart = build_chart_data(
            df=df,
            plan=plan,
        )

        charts.append(chart)

    return {
        "dataset_version_id":
            dataset_version_id,
        "analytics_ready": True,
        "chart_count": len(charts),
        "charts": charts,
    }

def get_dataset_suggested_questions(
    dataset_version_id: int,
    max_questions: int = 6,
) -> dict:
    """
    Return deterministic suggested analytical questions
    for a processed dataset version.
    """

    catalog = get_dataset_analytics_catalog(
        dataset_version_id
    )

    if not catalog["analytics_ready"]:
        return {
            "dataset_version_id":
                dataset_version_id,
            "analytics_ready": False,
            "question_count": 0,
            "questions": [],
        }

    questions = generate_question_suggestions(
        analytics_catalog=catalog,
        max_questions=max_questions,
    )

    return {
        "dataset_version_id":
            dataset_version_id,
        "analytics_ready": True,
        "question_count": len(questions),
        "questions": questions,
    }

def run_dataset_query(
    dataset_version_id: int,
    query: AnalyticsQueryRequest,
) -> dict:
    """
    Validate and execute one structured analytics query
    against a processed dataset version.
    """

    catalog = get_dataset_analytics_catalog(
        dataset_version_id
    )

    if not catalog["analytics_ready"]:
        raise ValueError(
            f"Dataset version {dataset_version_id} "
            "is not analytics-ready"
        )

    artifact = validate_query_against_catalog(
        query=query,
        analytics_catalog=catalog,
    )

    gold_df = read_gold_artifact(
        artifact["storage_path"]
    )

    result = execute_query(
        df=gold_df,
        query=query,
    )

    return {
        "dataset_version_id": dataset_version_id,
        "artifact": {
            "gold_artifact_id":
                artifact["gold_artifact_id"],
            "artifact_name":
                artifact["artifact_name"],
            "grain":
                artifact["grain"],
            "time_grain":
                artifact["time_grain"],
        },
        "query": query.model_dump(),
        "result": result,
    }

def ask_dataset(
    dataset_version_id: int,
    question: str,
) -> dict:
    """
    Convert a natural-language question into a validated
    analytics query and execute it against Gold data.
    """

    catalog = get_dataset_analytics_catalog(
        dataset_version_id
    )

    if not catalog["analytics_ready"]:
        raise ValueError(
            f"Dataset version {dataset_version_id} "
            "is not analytics-ready"
        )

    ai_context = build_ai_query_context(
        catalog
    )

    ai_plan = plan_analytics_query(
        question=question,
        ai_context=ai_context,
    )

    if ai_plan.status != "ANSWERABLE":
        return {
            "dataset_version_id": dataset_version_id,
            "question": question,
            "status": ai_plan.status,
            "message": ai_plan.message,
            "planned_query": None,
            "result": None,
        }

    if ai_plan.query is None:
        raise ValueError(
            "Answerable AI plan is missing a query"
        )

    query_result = run_dataset_query(
        dataset_version_id=dataset_version_id,
        query=ai_plan.query,
    )
    friendly_answer = generate_friendly_answer(
    question=question,
    planned_query=ai_plan.query.model_dump(),
    result=query_result["result"],
)

    return {
    "dataset_version_id": dataset_version_id,
    "question": question,
    "status": "ANSWERED",
    "message": ai_plan.message,
    "answer": friendly_answer,
    "planned_query": ai_plan.query.model_dump(),
    "result": query_result["result"],
}

def get_dataset_analytics_workspace(
    dataset_version_id: int,
) -> dict:
    catalog = get_dataset_analytics_catalog(
        dataset_version_id
    )

    if not catalog.get("analytics_ready"):
        return {
            "dataset_version_id": dataset_version_id,
            "analytics_ready": False,
            "kpis": [],
            "charts": [],
            "suggested_questions": [],
        }

    kpis = _build_kpis_from_catalog(
        catalog=catalog,
    )

    charts = _build_charts_from_catalog(
        catalog=catalog,
    )

    suggested_questions = (
        _build_suggestions_from_catalog(
            catalog=catalog,
        )
    )

    return {
        "dataset_version_id": dataset_version_id,
        "analytics_ready": True,
        "kpis": kpis,
        "charts": charts,
        "suggested_questions": suggested_questions,
    }

def _build_kpis_from_catalog(
    catalog: dict,
    max_kpis: int = 6,
) -> list[dict]:
    plans = discover_kpi_plans(
        catalog,
        max_kpis=max_kpis,
    )

    base_artifact = catalog.get("base_artifact")

    if not base_artifact:
        return []

    df = read_gold_artifact(
        base_artifact["storage_path"]
    )

    return calculate_kpis(
        df=df,
        plans=plans,
    )

def _build_charts_from_catalog(
    catalog: dict,
    max_charts: int = 4,
) -> list[dict]:
    plans = discover_chart_plans(
        catalog,
        max_charts=6,
    )

    selected_plans = select_dashboard_charts(
        plans,
        max_charts=max_charts,
    )

    charts = []

    for plan in selected_plans:
        artifact = _find_artifact(
            catalog=catalog,
            gold_artifact_id=plan.gold_artifact_id,
        )

        df = read_gold_artifact(
            artifact["storage_path"]
        )

        charts.append(
            build_chart_data(
                df=df,
                plan=plan,
            )
        )

    return charts

def _build_suggestions_from_catalog(
    catalog: dict,
    max_questions: int = 6,
) -> list[dict]:
    return generate_question_suggestions(
        catalog,
        max_questions=max_questions,
    )