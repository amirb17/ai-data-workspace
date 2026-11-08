"""Authenticated, scoped, read-only previews; no publication or SDK binding."""
from fastapi import APIRouter, Depends, HTTPException, Request, Path, Query
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.api.identity import get_current_user
from app.schemas.product_preview import CsvPreviewRequest,PreviewResponse,CapabilitiesResponse,JobList,GoldAssessmentResponse
from app.services import product_preview_service as service

router = APIRouter(prefix='/workspaces/{workspace_id}/datasets/{dataset_id}/product',tags=['Product readiness'])
MAX_REQUEST_BYTES = 2800000


def call(fn):
    try: return fn()
    except HTTPException: raise
    except PermissionError: raise HTTPException(404,'Product scope unavailable') from None
    except service.SourceMismatch: raise HTTPException(409,'Source bytes do not match the registered version') from None
    except service.PreviewBusy: raise HTTPException(429,'Preview capacity is busy; retry later') from None
    except Exception: raise HTTPException(503,'Product preview is temporarily unavailable') from None


@router.get('/capabilities',response_model=CapabilitiesResponse)
def capabilities(request:Request,workspace_id:int=Path(gt=0,le=9007199254740991),dataset_id:int=Path(gt=0,le=9007199254740991),user=Depends(get_current_user)):
    value = call(lambda:service.capabilities(workspace_id,dataset_id,user))
    if request.scope.get('datarise.public_launch'): value = {**value,'csv_upload':False}
    return value


@router.get('/jobs',response_model=JobList)
def jobs(workspace_id:int=Path(gt=0,le=9007199254740991),dataset_id:int=Path(gt=0,le=9007199254740991),user=Depends(get_current_user)):
    return call(lambda:service.jobs(workspace_id,dataset_id,user))


@router.post('/uploads/{upload_id}/csv-preview',response_model=PreviewResponse)
async def preview(request:Request,workspace_id:int=Path(gt=0,le=9007199254740991),dataset_id:int=Path(gt=0,le=9007199254740991),upload_id:int=Path(gt=0,le=9007199254740991),user=Depends(get_current_user)):
    # No request-body model in the signature: authentication and resource
    # authorization happen BEFORE reading, decoding or validating CSV input.
    await run_in_threadpool(call,lambda:service.authorize(workspace_id,dataset_id,user,upload_id))
    body = bytearray()
    async for chunk in request.stream():
        if len(body)+len(chunk)>MAX_REQUEST_BYTES: raise HTTPException(413,'Preview request exceeds its byte limit')
        body.extend(chunk)
    try: payload = CsvPreviewRequest.model_validate_json(body)
    except (ValidationError,ValueError): raise HTTPException(422,'Invalid preview request fields') from None
    return await run_in_threadpool(call,lambda:service.csv_preview(workspace_id,dataset_id,upload_id,user,payload))


@router.get('/uploads/{upload_id}/gold-assessment',response_model=GoldAssessmentResponse)
def gold(workspace_id:int=Path(gt=0,le=9007199254740991),dataset_id:int=Path(gt=0,le=9007199254740991),upload_id:int=Path(gt=0,le=9007199254740991),
         model_id:int=Query(gt=0,le=9007199254740991),version_id:int=Query(gt=0,le=9007199254740991),user=Depends(get_current_user)):
    return call(lambda:service.gold_assessment(workspace_id,dataset_id,upload_id,model_id,version_id,user))
