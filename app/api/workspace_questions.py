from fastapi import APIRouter, Depends, Body
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.schemas.semantic_suggestion import GenerateRequest
from app.schemas.workspace_questions import WorkspaceQuestionRequest
from app.services import workspace_question_service as service

router = APIRouter(prefix='/workspaces/{workspace_id}/questions', tags=['Ask your workspace'])


@router.post('')
def ask(workspace_id: int, request: WorkspaceQuestionRequest, user: dict = Depends(get_current_user)):
    return public_call(lambda: service.ask(workspace_id, user, request))


@router.get('/readiness')
def readiness(workspace_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: service.readiness(workspace_id, user))


@router.get('/answers')
def history(workspace_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: service.history(workspace_id, user))


@router.get('/answers/{answer_id}')
def answer(workspace_id: int, answer_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: service.history(workspace_id, user, answer_id))


@router.post('/answers/{answer_id}/refresh')
def refresh(workspace_id: int, answer_id: int, user: dict = Depends(get_current_user), request: GenerateRequest = Body(default=GenerateRequest())):
    # Ownership is checked before reading stored interpretation; clients cannot edit a plan.
    return public_call(lambda: service.ask(workspace_id, user, WorkspaceQuestionRequest(question='Refresh answer'), answer_id))
