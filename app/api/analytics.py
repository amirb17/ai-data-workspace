from fastapi import APIRouter, HTTPException, Depends
from app.api.identity import get_current_user
from app.services.processing_context_service import validate_scope
from app.db.database import get_connection


def authorize_version(dataset_version_id: int, user: dict = Depends(get_current_user)):
    with get_connection() as conn:
        row = conn.execute('SELECT d.workspace_id,d.dataset_id FROM dataset_versions v JOIN datasets d ON d.dataset_id=v.dataset_id WHERE v.dataset_version_id=%s', (dataset_version_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Dataset schema not found')
    try:
        validate_scope(user,*row)
    except PermissionError:
        raise HTTPException(403, 'Dataset access denied') from None

from app.services.analytics_service import (
    get_dataset_analytics_overview,
    get_dataset_kpis,
    get_dataset_charts,
    get_dataset_suggested_questions,
    run_dataset_query,
    ask_dataset,
)
from app.services.analytics_service import (
    get_dataset_analytics_workspace,
)

from app.schemas.analytics_query import (
    AnalyticsQueryRequest,
)
from app.schemas.ask_data import AskDataRequest


router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(authorize_version)],
)


@router.get(
    "/dataset-versions/{dataset_version_id}"
)
def get_analytics_overview(
    dataset_version_id: int,
):
    """
    Return available analytics models for a
    processed dataset version.
    """

    try:
        return get_dataset_analytics_overview(
            dataset_version_id
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get(
    "/dataset-versions/{dataset_version_id}/kpis"
)
def get_analytics_kpis(
    dataset_version_id: int,
):
    """
    Return automatically discovered and calculated KPIs
    for a processed dataset version.
    """

    try:
        return get_dataset_kpis(
            dataset_version_id
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

@router.get(
    "/dataset-versions/{dataset_version_id}/charts"
)
def get_analytics_charts(
    dataset_version_id: int,
):
    """
    Return dashboard-ready chart definitions and data
    for a processed dataset version.
    """

    try:
        return get_dataset_charts(
            dataset_version_id
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

@router.get(
    "/dataset-versions/{dataset_version_id}/suggestions"
)
def get_analytics_suggestions(
    dataset_version_id: int,
):
    """
    Return suggested analytical questions for a
    processed dataset version.
    """

    try:
        return get_dataset_suggested_questions(
            dataset_version_id
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

@router.post(
    "/dataset-versions/{dataset_version_id}/query"
)
def execute_analytics_query(
    dataset_version_id: int,
    query: AnalyticsQueryRequest,
):
    """
    Validate and execute a structured analytics query
    against a processed dataset version.
    """

    try:
        return run_dataset_query(
            dataset_version_id=dataset_version_id,
            query=query,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

@router.post(
    "/dataset-versions/{dataset_version_id}/ask"
)
def ask_dataset_question(
    dataset_version_id: int,
    request: AskDataRequest,
):
    """
    Ask a natural-language question about a processed
    dataset version.
    """

    try:
        return ask_dataset(
            dataset_version_id=dataset_version_id,
            question=request.question,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

@router.get(
    "/dataset-versions/{dataset_version_id}/workspace"
)
def dataset_analytics_workspace(
    dataset_version_id: int,
):
    try:
        return get_dataset_analytics_workspace(
            dataset_version_id
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
