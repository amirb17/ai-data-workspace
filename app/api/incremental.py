from fastapi import APIRouter, Depends, HTTPException
from app.api.identity import get_current_user
from app.schemas.incremental import LoadPolicyRequest, PrepareApplicationRequest, SnapshotDeliveryRequest
from app.services.snapshot_context_service import declare_snapshot
from app.services.incremental_policy_service import read_foundation, save_policy
from app.services.incremental_application_service import prepare_application
from app.services.append_application_service import apply_incremental

router = APIRouter(prefix='/workspaces/{workspace_id}/datasets/{dataset_id}/incremental',tags=['Incremental foundation'])


def public_call(operation):
    try:
        return operation()
    except PermissionError as exc:
        raise HTTPException(403,'Dataset or delivery access denied') from exc
    except LookupError as exc:
        raise HTTPException(404,'Application not found') from exc
    except ValueError as exc:
        raise HTTPException(400,'Invalid incremental policy or delivery context; check schema, keys and Silver approval') from exc
    except RuntimeError as exc:
        raise HTTPException(409,'Incremental state conflict; refresh policy and delivery state before retrying') from exc
    except Exception as exc:
        raise HTTPException(503,'Incremental configuration unavailable; refresh and retry') from exc


@router.get('')
def get_foundation(workspace_id:int,dataset_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:read_foundation(workspace_id,dataset_id,user))


@router.post('/policies')
def configure_policy(workspace_id:int,dataset_id:int,request:LoadPolicyRequest,user:dict=Depends(get_current_user)):
    return public_call(lambda:save_policy(workspace_id,dataset_id,user,request))


@router.post('/applications/prepare')
def prepare(workspace_id:int,dataset_id:int,request:PrepareApplicationRequest,user:dict=Depends(get_current_user)):
    return public_call(lambda:prepare_application(workspace_id,dataset_id,user,request.upload_request_id,request.policy_id))


@router.post('/deliveries/{upload_id}/apply')
def apply_delivery(workspace_id:int,dataset_id:int,upload_id:int,user:dict=Depends(get_current_user)):
    return public_call(lambda:apply_incremental(workspace_id,dataset_id,user,upload_id))


@router.post('/deliveries/{upload_id}/snapshot-context')
def snapshot_context(workspace_id:int,dataset_id:int,upload_id:int,request:SnapshotDeliveryRequest,user:dict=Depends(get_current_user)):
    return public_call(lambda:declare_snapshot(workspace_id,dataset_id,user,upload_id,request))
