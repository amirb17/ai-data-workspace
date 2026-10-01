from fastapi.testclient import TestClient

import app.api.analytics as analytics_api
from app.main import app


client = TestClient(app)


def test_workspace_returns_dashboard_payload(
    monkeypatch,
):
    def fake_workspace(
        dataset_version_id: int,
    ):
        return {
            "dataset_version_id": dataset_version_id,
            "analytics_ready": True,
            "kpis": [
                {
                    "kpi_name": "amount_sum",
                    "label": "Total Amount",
                    "source_column": "amount",
                    "aggregation": "SUM",
                    "value": 7650,
                }
            ],
            "charts": [
                {
                    "chart_name": "city_amount_chart",
                    "title": "Amount by City",
                    "chart_type": "BAR",
                    "data": [],
                }
            ],
            "suggested_questions": [
                {
                    "question": (
                        "Which city has the highest "
                        "total amount?"
                    )
                }
            ],
        }

    monkeypatch.setattr(
        analytics_api,
        "get_dataset_analytics_workspace",
        fake_workspace,
    )

    response = client.get(
        "/analytics/dataset-versions/7/workspace"
    )

    assert response.status_code == 200

    body = response.json()

    assert body["dataset_version_id"] == 7
    assert body["analytics_ready"] is True
    assert len(body["kpis"]) == 1
    assert len(body["charts"]) == 1
    assert len(body["suggested_questions"]) == 1


def test_ask_returns_answered_response(
    monkeypatch,
):
    def fake_ask_dataset(
        dataset_version_id: int,
        question: str,
    ):
        return {
            "dataset_version_id": dataset_version_id,
            "question": question,
            "status": "ANSWERED",
            "message": "Query completed.",
            "answer": (
                "Electronics has the highest "
                "total amount."
            ),
            "planned_query": {
                "artifact_name": (
                    "product_category_summary"
                ),
                "select": [
                    "product_category",
                    "amount_sum",
                ],
                "filters": [],
                "sort": [
                    {
                        "column": "amount_sum",
                        "direction": "DESC",
                    }
                ],
                "limit": 1,
            },
            "result": {
                "columns": [
                    "product_category",
                    "amount_sum",
                ],
                "row_count": 1,
                "rows": [
                    {
                        "product_category":
                            "Electronics",
                        "amount_sum": 3250,
                    }
                ],
            },
        }

    monkeypatch.setattr(
        analytics_api,
        "ask_dataset",
        fake_ask_dataset,
    )

    response = client.post(
        "/analytics/dataset-versions/7/ask",
        json={
            "question": (
                "Which product category has the "
                "highest total amount?"
            )
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "ANSWERED"
    assert body["answer"]
    assert body["result"]["row_count"] == 1


def test_ask_returns_not_relevant(
    monkeypatch,
):
    def fake_ask_dataset(
        dataset_version_id: int,
        question: str,
    ):
        return {
            "dataset_version_id": dataset_version_id,
            "question": question,
            "status": "NOT_RELEVANT",
            "message": (
                "The question is not relevant "
                "to the dataset."
            ),
            "planned_query": None,
            "result": None,
        }

    monkeypatch.setattr(
        analytics_api,
        "ask_dataset",
        fake_ask_dataset,
    )

    response = client.post(
        "/analytics/dataset-versions/7/ask",
        json={
            "question": "What is the weather today?"
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "NOT_RELEVANT"
    assert body["planned_query"] is None
    assert body["result"] is None


def test_ask_rejects_whitespace_question():
    response = client.post(
        "/analytics/dataset-versions/7/ask",
        json={
            "question": "   "
        },
    )

    assert response.status_code == 422


def test_ask_maps_provider_failure_to_503(
    monkeypatch,
):
    def fake_ask_dataset(
        dataset_version_id: int,
        question: str,
    ):
        raise RuntimeError(
            "AI provider temporarily unavailable"
        )

    monkeypatch.setattr(
        analytics_api,
        "ask_dataset",
        fake_ask_dataset,
    )

    response = client.post(
        "/analytics/dataset-versions/7/ask",
        json={
            "question": (
                "Which city has the highest amount?"
            )
        },
    )

    assert response.status_code == 503

    assert response.json()["detail"] == (
        "AI provider temporarily unavailable"
    )


def test_workspace_maps_service_error_to_400(
    monkeypatch,
):
    def fake_workspace(
        dataset_version_id: int,
    ):
        raise ValueError(
            "Dataset version is not analytics-ready"
        )

    monkeypatch.setattr(
        analytics_api,
        "get_dataset_analytics_workspace",
        fake_workspace,
    )

    response = client.get(
        "/analytics/dataset-versions/999/workspace"
    )

    assert response.status_code == 400

    assert response.json()["detail"] == (
        "Dataset version is not analytics-ready"
    )