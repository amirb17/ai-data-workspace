from fastapi import APIRouter, Body, Depends, HTTPException
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.services.dataset_understanding_service import read_understanding, generate_understanding
from app.schemas.semantic_suggestion import Understanding, GenerateRequest

router = APIRouter(prefix='/workspaces/{workspace_id}/datasets/{dataset_id}/semantic-understanding',tags=['Dataset understanding'])


@router.get('',response_model=Understanding)
def read(workspace_id:int,dataset_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:read_understanding(workspace_id,dataset_id,user))


@router.get('/readiness',response_model=Understanding)
def readiness(workspace_id:int,dataset_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:read_understanding(workspace_id,dataset_id,user,include_content=False))


@router.post('/generate',response_model=Understanding)
def generate(workspace_id:int,dataset_id:int,user:dict=Depends(get_current_user),request:GenerateRequest=Body(default=GenerateRequest())):
    try:
        return public_call(lambda:generate_understanding(workspace_id,dataset_id,user))
    except HTTPException as exc:
        if exc.status_code == 400:
            raise HTTPException(400,'Refresh the Data Profile before analyzing. V1 supports at most 200 columns and 60 KB of evidence.') from None
        raise
