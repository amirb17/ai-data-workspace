from fastapi import APIRouter, Body, Depends
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.schemas.semantic_suggestion import GenerateRequest
from app.services.workspace_analytics_service import read_analytics, refresh_analytics

router = APIRouter(prefix='/workspaces/{workspace_id}/analytics', tags=['Workspace analytics'])


@router.get('')
@router.get('/readiness')
def read(workspace_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: read_analytics(workspace_id, user))


@router.post('/refresh')
def refresh(workspace_id: int, user: dict = Depends(get_current_user), request: GenerateRequest = Body(default=GenerateRequest())):
    return public_call(lambda: refresh_analytics(workspace_id, user))


@router.post('/metrics/{candidate_id}/retry')
def retry(workspace_id: int, candidate_id: int, user: dict = Depends(get_current_user), request: GenerateRequest = Body(default=GenerateRequest())):
    return public_call(lambda: refresh_analytics(workspace_id, user, candidate_id))
