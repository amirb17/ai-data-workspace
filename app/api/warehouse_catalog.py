"""Authenticated read-only warehouse model catalog."""
from fastapi import APIRouter, Depends
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.services import warehouse_catalog_service, warehouse_relationship_service
from app.warehouse.relationship_contract import WarehouseRelationship

router = APIRouter(
    prefix="/workspaces/{workspace_id}/warehouse",
    tags=["Warehouse models"],
)


@router.get("/catalog")
def catalog(workspace_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: warehouse_catalog_service.snapshot(workspace_id, user))


@router.get("/relationships")
def relationships(workspace_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: {
        "workspace_id": workspace_id,
        "relationships": warehouse_relationship_service.list_current(workspace_id, user),
        "query_execution_enabled": False,
    })


@router.post("/relationships", status_code=201)
def register_relationship(
    workspace_id: int,
    request: WarehouseRelationship,
    user: dict = Depends(get_current_user),
):
    return public_call(lambda: warehouse_relationship_service.register(workspace_id, user, request))
