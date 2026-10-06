from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.api.identity import get_current_user
from app.db.workspace_repository import create_workspace, get_workspace_by_id, list_workspaces_by_owner
from app.db.dataset_repository import get_datasets_by_workspace, get_dataset_by_workspace
from app.services.dataset_service import get_or_create_dataset
from app.services.dataset_processing_service import read_dataset_processing, process_pending
from app.api.processing_errors import public_call

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("/{workspace_id}/datasets/{dataset_id}/processing")
def dataset_processing_endpoint(workspace_id: int, dataset_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: read_dataset_processing(workspace_id, dataset_id, user))


@router.post("/{workspace_id}/datasets/{dataset_id}/processing/pending")
def process_pending_endpoint(workspace_id: int, dataset_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: process_pending(workspace_id, dataset_id, user))


class WorkspaceCreateRequest(BaseModel):
    workspace_name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    owner: str | None = None  # Legacy field: never grants ownership.


class DatasetCreateRequest(BaseModel):
    dataset_name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    owner: str | None = None


def owned_workspace(workspace_id: int, user: dict):
    workspace = get_workspace_by_id(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if workspace[3] != user["owner_key"]:
        raise HTTPException(status_code=403, detail="Workspace access denied")
    return workspace


def workspace_payload(row):
    return dict(zip(("workspace_id", "workspace_name", "description", "owner", "status", "created_at", "updated_at"), row))


def dataset_payload(row):
    return dict(zip(("dataset_id", "dataset_name", "description", "owner", "status", "created_at", "updated_at", "workspace_id"), row))


@router.get("")
def list_workspaces_endpoint(user: dict = Depends(get_current_user)):
    return {"user_id": user["user_id"], "workspaces": [workspace_payload(row) for row in list_workspaces_by_owner(user["owner_key"])]}


@router.post("")
def create_workspace_endpoint(request: WorkspaceCreateRequest, user: dict = Depends(get_current_user)):
    if not request.workspace_name.strip():
        raise HTTPException(status_code=400, detail="Workspace name is required")
    workspace, created = create_workspace(workspace_name=request.workspace_name.strip(), description=request.description, owner=user["owner_key"])
    return {**workspace_payload(workspace), "created": created}


@router.get("/{workspace_id}")
def get_workspace_endpoint(workspace_id: int, user: dict = Depends(get_current_user)):
    return workspace_payload(owned_workspace(workspace_id, user))


@router.post("/{workspace_id}/datasets")
def create_dataset_endpoint(workspace_id: int, request: DatasetCreateRequest, user: dict = Depends(get_current_user)):
    workspace = owned_workspace(workspace_id, user)
    if not request.dataset_name.strip():
        raise HTTPException(status_code=400, detail="Dataset name is required")
    return get_or_create_dataset(workspace_id=workspace_id, dataset_name=request.dataset_name.strip(), description=request.description, owner=workspace[3])


@router.get("/{workspace_id}/datasets")
def list_datasets_endpoint(workspace_id: int, user: dict = Depends(get_current_user)):
    owned_workspace(workspace_id, user)
    return {"workspace_id": workspace_id, "datasets": [dataset_payload(row) for row in get_datasets_by_workspace(workspace_id=workspace_id)]}


@router.get("/{workspace_id}/datasets/{dataset_id}")
def get_dataset_endpoint(workspace_id: int, dataset_id: int, user: dict = Depends(get_current_user)):
    owned_workspace(workspace_id, user)
    dataset = get_dataset_by_workspace(workspace_id=workspace_id, dataset_id=dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found in workspace")
    return dataset_payload(dataset)
