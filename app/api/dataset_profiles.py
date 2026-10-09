from fastapi import APIRouter,Depends
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.services.dataset_profile_service import read_profile,readiness,refresh_profile
from app.schemas.semantic_evidence import SemanticEvidenceBundle

router=APIRouter(prefix='/workspaces/{workspace_id}/datasets/{dataset_id}/profile',tags=['Dataset profile'])


@router.get('',response_model=SemanticEvidenceBundle)
def read(workspace_id:int,dataset_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:read_profile(workspace_id,dataset_id,user))


@router.get('/readiness',response_model=SemanticEvidenceBundle)
def status(workspace_id:int,dataset_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:readiness(workspace_id,dataset_id,user))


@router.post('/refresh',response_model=SemanticEvidenceBundle)
def refresh(workspace_id:int,dataset_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:refresh_profile(workspace_id,dataset_id,user))
