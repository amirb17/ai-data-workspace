from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.workspace_repository import (
    create_workspace,
    get_workspace_by_id,
)
from app.db.dataset_repository import (
    get_datasets_by_workspace,
)
from app.services.dataset_service import (
    get_or_create_dataset,
)


router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
)


class WorkspaceCreateRequest(BaseModel):
    workspace_name: str
    description: str | None = None
    owner: str


class DatasetCreateRequest(BaseModel):
    dataset_name: str
    description: str | None = None
    owner: str | None = None


@router.post("")
def create_workspace_endpoint(
    request: WorkspaceCreateRequest,
):
    workspace, created = create_workspace(
        workspace_name=request.workspace_name,
        description=request.description,
        owner=request.owner,
    )

    return {
        "workspace_id": workspace[0],
        "workspace_name": workspace[1],
        "description": workspace[2],
        "owner": workspace[3],
        "status": workspace[4],
        "created_at": workspace[5],
        "updated_at": workspace[6],
        "created": created,
    }


@router.get("/{workspace_id}")
def get_workspace_endpoint(
    workspace_id: int,
):
    workspace = get_workspace_by_id(workspace_id)

    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail=f"Workspace {workspace_id} not found",
        )

    return {
        "workspace_id": workspace[0],
        "workspace_name": workspace[1],
        "description": workspace[2],
        "owner": workspace[3],
        "status": workspace[4],
        "created_at": workspace[5],
        "updated_at": workspace[6],
    }


@router.post("/{workspace_id}/datasets")
def create_dataset_endpoint(
    workspace_id: int,
    request: DatasetCreateRequest,
):
    workspace = get_workspace_by_id(workspace_id)

    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail=f"Workspace {workspace_id} not found",
        )

    # Until authentication is introduced, inherit ownership from
    # the workspace rather than trusting an arbitrary dataset owner.
    dataset = get_or_create_dataset(
        workspace_id=workspace_id,
        dataset_name=request.dataset_name,
        description=request.description,
        owner=workspace[3],
    )

    return dataset


@router.get("/{workspace_id}/datasets")
def list_datasets_endpoint(
    workspace_id: int,
):
    workspace = get_workspace_by_id(workspace_id)

    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail=f"Workspace {workspace_id} not found",
        )

    datasets = get_datasets_by_workspace(
        workspace_id=workspace_id,
    )

    return {
        "workspace_id": workspace_id,
        "datasets": [
            {
                "dataset_id": dataset[0],
                "dataset_name": dataset[1],
                "description": dataset[2],
                "owner": dataset[3],
                "status": dataset[4],
                "created_at": dataset[5],
                "updated_at": dataset[6],
                "workspace_id": dataset[7],
            }
            for dataset in datasets
        ],
    }