"""Authenticated read-only warehouse model catalog."""
from fastapi import APIRouter, Depends, HTTPException, Request
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


@router.get("/queries/{attempt_handle}/result-readiness")
def query_result_readiness(
    workspace_id: int, attempt_handle: str, request: Request,
    user: dict = Depends(get_current_user),
):
    """Session-bound read-only status. Never returns BigQuery job ID or rows.

    The existing authenticated dependency enforces the application's
    principal; the service independently rechecks the original cookie,
    workspace, attempt owner/session and settled-success evidence.
    """
    from app.db.warehouse_query_attempt_repository import WarehouseAttemptConflict
    from app.services.warehouse_sql_authenticated_result_service import (
        inspect_authenticated_warehouse_result_without_fetch,
    )
    from app.services.warehouse_sql_result_eligibility_service import ResultEligibilityConflict

    try:
        status = inspect_authenticated_warehouse_result_without_fetch(
            request=request, workspace_id=workspace_id, attempt_handle=attempt_handle,
        )
    except HTTPException:
        raise
    except WarehouseAttemptConflict:
        raise HTTPException(404, "Warehouse query not found") from None
    except ResultEligibilityConflict:
        raise HTTPException(409, "Warehouse query results are not ready") from None
    except ValueError:
        raise HTTPException(400, "Invalid warehouse result request") from None
    except Exception:
        raise HTTPException(503, "Warehouse result readiness unavailable") from None
    return {
        "attempt_handle": status.attempt_handle,
        "status": "RESULT_ELIGIBLE",
        "max_page_rows": status.max_page_rows,
        "results_available": False,
        "query_execution_enabled": False,
    }
