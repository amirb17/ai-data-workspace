from fastapi import APIRouter, Body, Depends
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.schemas.semantic_suggestion import GenerateRequest
from app.schemas.relationships import RelationshipsView, ReviewRequest
from app.services.relationship_service import read_relationships, discover, review

router = APIRouter(prefix='/workspaces/{workspace_id}/relationships',tags=['Workspace relationships'])

@router.get('',response_model=RelationshipsView)
def read(workspace_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:read_relationships(workspace_id,user))

@router.get('/readiness',response_model=RelationshipsView)
def readiness(workspace_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:read_relationships(workspace_id,user,False))

@router.post('/discover',response_model=RelationshipsView)
def generate(workspace_id:int,user:dict=Depends(get_current_user),request:GenerateRequest=Body(default=GenerateRequest())):
    return public_call(lambda:discover(workspace_id,user))

@router.post('/candidates/{candidate_id}/confirm',response_model=RelationshipsView)
def confirm(workspace_id:int,candidate_id:int,request:ReviewRequest,user:dict=Depends(get_current_user)):
    return public_call(lambda:review(workspace_id,candidate_id,user,'CONFIRMED',request.expected_review_version))

@router.post('/candidates/{candidate_id}/reject',response_model=RelationshipsView)
def reject(workspace_id:int,candidate_id:int,request:ReviewRequest,user:dict=Depends(get_current_user)):
    return public_call(lambda:review(workspace_id,candidate_id,user,'REJECTED',request.expected_review_version))
