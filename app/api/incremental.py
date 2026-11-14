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


@router.post('/initial-append-policy/uploads/{upload_id}')
def initial_append_policy(workspace_id:int,dataset_id:int,upload_id:int,
                          user:dict=Depends(get_current_user)):
    """Idempotent, owner-scoped first APPEND policy from verified Silver.

    The database SECURITY DEFINER function owns the narrowly bounded INSERT.
    This API has no table-write privileges and does not run application I/O.
    """
    from app.db.database import repository_transaction
    from app.services.processing_context_service import validate_scope

    def operation():
        validate_scope(user,workspace_id,dataset_id)
        with repository_transaction() as conn:
            upload = conn.execute(
                'SELECT 1 FROM upload_requests WHERE upload_id=%s AND '
                'workspace_id=%s AND dataset_id=%s',
                (upload_id,workspace_id,dataset_id)).fetchone()
            if upload is None:
                raise PermissionError('Upload is outside the requested dataset')
            policy = conn.execute('SELECT task20_initial_append_policy(%s,%s)',
                                  (user['user_id'],upload_id)).fetchone()[0]
            return {'workspace_id':workspace_id, 'dataset_id':dataset_id,
                    'upload_id':upload_id, 'policy_id':policy,
                    'load_strategy':'APPEND', 'schema_evolution_policy':'STRICT'}
    return public_call(operation)
