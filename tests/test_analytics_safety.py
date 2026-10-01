import pandas as pd
import pytest
from pydantic import ValidationError

from app.ai.query_plan_normalizer import (
    normalize_query_plan,
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
from app.schemas.ask_data import AskDataRequest


def build_test_catalog() -> dict:
    """
    Small fake semantic catalog for unit tests.

    No PostgreSQL, S3 or Gemini calls are required.
    """

    return {
        "dataset_version_id": 1,
        "analytics_ready": True,
        "base_artifact": None,
        "marts": [
            {
                "gold_artifact_id": 10,
                "artifact_name": "category_summary",
                "artifact_type": "MART",
                "storage_path": "test/path.parquet",
                "row_count": 3,
                "grain": "category",
                "time_grain": None,
                "dimensions": [
                    "category",
                ],
                "measures": [
                    "value_sum",
                ],
                "metrics": [
                    "record_count",
                ],
                "columns": [
                    {
                        "column_name": "category",
                        "column_role": "DIMENSION",
                        "source_column": "category",
                        "aggregation_type": None,
                        "ordinal_position": 1,
                        "data_type": "str",
                    },
                    {
                        "column_name": "value_sum",
                        "column_role": "MEASURE",
                        "source_column": "value",
                        "aggregation_type": "SUM",
                        "ordinal_position": 2,
                        "data_type": "int64",
                    },
                    {
                        "column_name": "record_count",
                        "column_role": "METRIC",
                        "source_column": None,
                        "aggregation_type": "COUNT",
                        "ordinal_position": 3,
                        "data_type": "int64",
                    },
                ],
            }
        ],
    }


def test_whitespace_question_is_rejected():
    with pytest.raises(ValidationError):
        AskDataRequest(
            question="   "
        )


def test_top_two_normalizes_limit():
    query = AnalyticsQueryRequest(
        artifact_name="category_summary",
        select=[
            "category",
            "value_sum",
        ],
        sort=[
            {
                "column": "value_sum",
                "direction": "DESC",
            }
        ],
        limit=100,
    )

    normalized = normalize_query_plan(
        question="Show the top two categories",
        query=query,
    )

    assert normalized.limit == 2


def test_duplicate_sort_is_removed():
    query = AnalyticsQueryRequest(
        artifact_name="category_summary",
        select=[
            "category",
            "value_sum",
        ],
        sort=[
            {
                "column": "value_sum",
                "direction": "DESC",
            },
            {
                "column": "value_sum",
                "direction": "DESC",
            },
        ],
    )

    normalized = normalize_query_plan(
        question="Show categories",
        query=query,
    )

    assert len(normalized.sort) == 1


def test_unknown_artifact_is_rejected():
    catalog = build_test_catalog()

    query = AnalyticsQueryRequest(
        artifact_name="fake_table",
        select=["category"],
    )

    with pytest.raises(
        ValueError,
        match="Unknown analytics artifact",
    ):
        validate_query_against_catalog(
            query=query,
            analytics_catalog=catalog,
        )


def test_unknown_column_is_rejected():
    catalog = build_test_catalog()

    query = AnalyticsQueryRequest(
        artifact_name="category_summary",
        select=[
            "category",
            "secret_column",
        ],
    )

    with pytest.raises(
        ValueError,
        match="Unknown selected column",
    ):
        validate_query_against_catalog(
            query=query,
            analytics_catalog=catalog,
        )


def test_invalid_in_filter_is_rejected():
    catalog = build_test_catalog()

    query = AnalyticsQueryRequest(
        artifact_name="category_summary",
        filters=[
            {
                "column": "category",
                "operator": "IN",
                "value": "A",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="IN filter requires a list",
    ):
        validate_query_against_catalog(
            query=query,
            analytics_catalog=catalog,
        )


def test_query_limit_above_1000_is_rejected():
    with pytest.raises(ValidationError):
        AnalyticsQueryRequest(
            artifact_name="category_summary",
            limit=50000,
        )


def test_query_executor_returns_ranked_rows():
    df = pd.DataFrame(
        [
            {
                "category": "A",
                "value_sum": 100,
            },
            {
                "category": "B",
                "value_sum": 300,
            },
            {
                "category": "C",
                "value_sum": 200,
            },
        ]
    )

    query = AnalyticsQueryRequest(
        artifact_name="category_summary",
        select=[
            "category",
            "value_sum",
        ],
        sort=[
            {
                "column": "value_sum",
                "direction": "DESC",
            }
        ],
        limit=2,
    )

    result = execute_query(
        df=df,
        query=query,
    )

    assert result["row_count"] == 2

    assert result["rows"] == [
        {
            "category": "B",
            "value_sum": 300,
        },
        {
            "category": "C",
            "value_sum": 200,
        },
    ]


def test_query_with_no_matches_returns_zero_rows():
    df = pd.DataFrame(
        [
            {
                "category": "A",
                "value_sum": 100,
            },
            {
                "category": "B",
                "value_sum": 200,
            },
        ]
    )

    query = AnalyticsQueryRequest(
        artifact_name="category_summary",
        select=[
            "category",
            "value_sum",
        ],
        filters=[
            {
                "column": "value_sum",
                "operator": "GT",
                "value": 1000,
            }
        ],
    )

    result = execute_query(
        df=df,
        query=query,
    )

    assert result["row_count"] == 0
    assert result["rows"] == []