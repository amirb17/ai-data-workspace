"""Authenticated read-only warehouse model catalog."""
from fastapi import APIRouter, Depends
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.services import warehouse_catalog_service

router = APIRouter(
    prefix="/workspaces/{workspace_id}/warehouse",
    tags=["Warehouse models"],
)


@router.get("/catalog")
def catalog(workspace_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: warehouse_catalog_service.snapshot(workspace_id, user))
