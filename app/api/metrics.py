from fastapi import APIRouter, Body, Depends
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.schemas.metrics import MetricReview
from app.schemas.semantic_suggestion import GenerateRequest
from app.services.metric_service import read_metrics,discover_metrics,review_metric

router=APIRouter(prefix='/workspaces/{workspace_id}/metrics',tags=['Workspace metric definitions'])
@router.get('')
def read(workspace_id:int,user:dict=Depends(get_current_user)):return public_call(lambda:read_metrics(workspace_id,user))
@router.get('/readiness')
def readiness(workspace_id:int,user:dict=Depends(get_current_user)):return public_call(lambda:read_metrics(workspace_id,user,False))
@router.post('/discover')
def discover(workspace_id:int,user:dict=Depends(get_current_user),request:GenerateRequest=Body(default=GenerateRequest())):return public_call(lambda:discover_metrics(workspace_id,user))
@router.post('/candidates/{candidate_id}/approve')
def approve(workspace_id:int,candidate_id:int,request:MetricReview,user:dict=Depends(get_current_user)):return public_call(lambda:review_metric(workspace_id,candidate_id,user,'APPROVED',request.expected_review_version))
@router.post('/candidates/{candidate_id}/reject')
def reject(workspace_id:int,candidate_id:int,request:MetricReview,user:dict=Depends(get_current_user)):return public_call(lambda:review_metric(workspace_id,candidate_id,user,'REJECTED',request.expected_review_version))
