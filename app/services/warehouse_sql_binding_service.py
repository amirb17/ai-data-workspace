"""Step 409B: owned PostgreSQL resolution of an exact warehouse SQL draft.

Read-only binding resolution only. No public API, SQL execution, or provider
access. Reuses the existing immutable serving-receipt digest verifier.
An exact published source-state pin is required, rather than guessing between
models, generations, or source versions.
"""
from fastapi import HTTPException

from app.db.database import repository_transaction
from app.db import warehouse_repository as repository
from app.services.metric_warehouse_binding_service import _publication_state_id, _source
from app.warehouse.access import owned_workspace, owned_version, validate_sources
from app.warehouse.config import require_enabled
from app.warehouse.sql_draft_planner import WarehouseSqlDraft
from app.warehouse.sql_physical_binding import InternalDraftBinding, bind_draft


def resolve_single_draft(
    workspace_id: int,
    user: dict,
    draft: WarehouseSqlDraft,
    *,
    source_dataset_id: int,
    source_state_id: int,
) -> InternalDraftBinding:
    """Resolve a single-model SQL draft through current, exact metadata pins.

    The supplied draft was compiled separately against an authorized catalog.
    This service independently verifies workspace ownership, current approved
    model/version, ACTIVE source membership, publication head, registered
    BigQuery resource and the complete immutable serving receipt.

    Result is INTERNAL ONLY: contains a physical table; never serialize or
    expose it through an HTTP endpoint. Still not evidence of a live BigQuery
    table, and does not grant query permissions.
    """
    if (type(workspace_id) is not int or workspace_id < 1
            or type(source_dataset_id) is not int or source_dataset_id < 1
            or type(source_state_id) is not int or source_state_id < 1):
        raise ValueError("Positive warehouse, source dataset and state IDs are required")
    if type(draft) is not WarehouseSqlDraft or draft.executable is not False:
        raise ValueError("A non-executable single-model SQL draft is required")
    require_enabled()

    with repository_transaction() as conn:
        owned_workspace(conn, workspace_id, user)
        model, version = owned_version(
            conn, workspace_id, draft.model_id, draft.model_version_id, user
        )
        if model["status"] != "APPROVED" or version["status"] != "APPROVED":
            raise HTTPException(409, "Warehouse model/version is no longer approved")
        mappings = validate_sources(
            conn, workspace_id, draft.model_version_id, user["owner_key"]
        )
        primaries = [m for m in mappings if m["purpose"] == "PRIMARY"]
        if len(primaries) != 1 or primaries[0]["source_dataset_id"] != source_dataset_id:
            raise HTTPException(409, "Exactly one reviewed PRIMARY source must match")
        rows = repository.metric_warehouse_bindings(
            conn, workspace_id, [source_dataset_id], "development"
        )
        exact = [
            row for row in rows
            if row["warehouse_model_id"] == draft.model_id
            and row["warehouse_model_version_id"] == draft.model_version_id
            and _publication_state_id(row) == source_state_id
        ]
        if len(exact) != 1:
            raise HTTPException(409, "Unique current publication and source state are required")
        # _source verifies configuration, schema, source vector, serving
        # token/intent digests, generation and current publication revision.
        resolved = _source(exact[0], workspace_id, source_state_id)
        return bind_draft(draft, fact=resolved)
