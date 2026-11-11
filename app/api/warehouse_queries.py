"""Workspace-owned warehouse query API; provider dependency defaults to unavailable."""
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.api.identity import get_current_user
from app.services import warehouse_query_workflow_service as workflow

router=APIRouter(prefix='/workspaces/{workspace_id}/warehouse/queries',tags=['Warehouse queries'])

class QueryInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    model_id:int=Field(gt=0)
    sql:str=Field(min_length=1,max_length=16000)

class AdmissionInput(QueryInput):
    idempotency_key:str=Field(min_length=16,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')


def _call(operation):
    from app.db.warehouse_query_attempt_repository import WarehouseAttemptConflict
    from app.db.query_governance_repository import ReservationConflict
    from app.services.warehouse_sql_result_eligibility_service import ResultEligibilityConflict
    try:return operation()
    except HTTPException:raise
    except WarehouseAttemptConflict:raise HTTPException(404,'Warehouse query unavailable') from None
    except (workflow.repo.RecoveryConflict,workflow.policy.PolicyConflict,ReservationConflict,ResultEligibilityConflict):
        raise HTTPException(409,'Query state requires recovery or is not ready') from None
    except ValueError:raise HTTPException(400,'Invalid or stale warehouse query') from None
    except Exception:raise HTTPException(503,'Warehouse query operation unavailable; retain the same request identity') from None

@router.post('/validate')
def validate(workspace_id:int,body:QueryInput,request:Request,user=Depends(get_current_user),runtime=Depends(workflow.get_query_runtime)):
    return _call(lambda:workflow.validate_query(request,workspace_id,body.model_id,body.sql,runtime))

@router.post('',status_code=201)
def admit(workspace_id:int,body:AdmissionInput,request:Request,user=Depends(get_current_user),runtime=Depends(workflow.get_query_runtime)):
    return _call(lambda:workflow.admit_query(request,workspace_id,body.model_id,body.sql,body.idempotency_key,runtime))

@router.get('/{attempt_handle}')
def status(workspace_id:int,attempt_handle:str,request:Request,user=Depends(get_current_user)):
    return _call(lambda:workflow.query_status(request,workspace_id,attempt_handle))

@router.post('/{attempt_handle}/execute')
def execute(workspace_id:int,attempt_handle:str,request:Request,user=Depends(get_current_user),runtime=Depends(workflow.get_query_runtime)):
    return _call(lambda:workflow.execute_query(request,workspace_id,attempt_handle,runtime))

@router.post('/{attempt_handle}/refresh')
def refresh(workspace_id:int,attempt_handle:str,request:Request,user=Depends(get_current_user),runtime=Depends(workflow.get_query_runtime)):
    return _call(lambda:workflow.refresh_query(request,workspace_id,attempt_handle,runtime))

@router.post('/{attempt_handle}/cancel')
def cancel(workspace_id:int,attempt_handle:str,request:Request,user=Depends(get_current_user),runtime=Depends(workflow.get_query_runtime)):
    return _call(lambda:workflow.cancel_query(request,workspace_id,attempt_handle,runtime))

@router.get('/{attempt_handle}/results')
def results(workspace_id:int,attempt_handle:str,request:Request,user=Depends(get_current_user),runtime=Depends(workflow.get_query_runtime)):
    return _call(lambda:workflow.query_results(request,workspace_id,attempt_handle,runtime))
