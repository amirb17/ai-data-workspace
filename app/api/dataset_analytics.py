from fastapi import APIRouter, Depends, HTTPException
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.schemas.ask_data import AskDataRequest
from app.schemas.analytics_query import AnalyticsQueryRequest
from app.services.dataset_analytics_service import readiness, refresh_analytics, read_dashboard
from app.services.analytics_service import ask_dataset, run_dataset_query

router = APIRouter(prefix='/workspaces/{workspace_id}/datasets/{dataset_id}/analytics', tags=['Dataset analytics'])


@router.get('')
def read(workspace_id: int, dataset_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: read_dashboard(workspace_id,dataset_id,user))


@router.get('/readiness')
def status(workspace_id: int, dataset_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: readiness(workspace_id,dataset_id,user))


@router.post('/refresh')
def refresh(workspace_id: int, dataset_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: refresh_analytics(workspace_id,dataset_id,user))


def ready_version(workspace_id,dataset_id,user):
    state = readiness(workspace_id,dataset_id,user)
    if not state['analytics_ready']:
        raise HTTPException(409, 'Analytics refresh required before querying the trusted dataset')
    from app.db.database import get_connection
    with get_connection() as conn:
        return conn.execute('SELECT dataset_version_id FROM dataset_state_versions WHERE state_id=%s', (state['current_state_id'],)).fetchone()[0]


@router.post('/ask')
def ask(workspace_id: int, dataset_id: int, request: AskDataRequest, user: dict = Depends(get_current_user)):
    version = public_call(lambda: ready_version(workspace_id,dataset_id,user))
    result = public_call(lambda: ask_dataset(version,request.question))
    # Do not return an answer if an update published during the AI/query operation.
    public_call(lambda: ready_version(workspace_id,dataset_id,user))
    return result


@router.post('/query')
def query(workspace_id: int, dataset_id: int, request: AnalyticsQueryRequest, user: dict = Depends(get_current_user)):
    version = public_call(lambda: ready_version(workspace_id,dataset_id,user))
    result = public_call(lambda: run_dataset_query(version,request))
    public_call(lambda: ready_version(workspace_id,dataset_id,user))
    return result
