from fastapi import APIRouter, Body, Depends, HTTPException
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.schemas.semantic_suggestion import GenerateRequest
from app.schemas.workspace_semantics import WorkspaceUnderstanding
from app.services.workspace_understanding_service import read_understanding_workspace, generate_workspace

router=APIRouter(prefix='/workspaces/{workspace_id}/semantic-understanding',tags=['Workspace understanding'])

@router.get('',response_model=WorkspaceUnderstanding)
def read(workspace_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:read_understanding_workspace(workspace_id,user))

@router.get('/readiness',response_model=WorkspaceUnderstanding)
def readiness(workspace_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:read_understanding_workspace(workspace_id,user,False))

@router.post('/generate',response_model=WorkspaceUnderstanding)
def generate(workspace_id:int,user:dict=Depends(get_current_user),request:GenerateRequest=Body(default=GenerateRequest())):
    try:
        return public_call(lambda:generate_workspace(workspace_id,user))
    except HTTPException as exc:
        if exc.status_code==400:
            raise HTTPException(400,'Reload current workspace evidence. At least one eligible dataset is required; V1 limits are 50 datasets, 1000 columns and 100 KB.') from None
        raise
