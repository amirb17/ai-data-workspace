"""Step 409C: owned, version-pinned, two-model warehouse SQL binding.

A new relationship is never inferred from SQL or accepted solely from a
client-provided draft. Resolve a persisted, currently valid reviewed
relationship, then independently pin each model's publication and serving
receipt in one PostgreSQL transaction. No provider requests or SQL execution.
"""
from fastapi import HTTPException
from psycopg.rows import dict_row

from app.db.database import repository_transaction
from app.db import warehouse_repository as repository
from app.services.metric_warehouse_binding_service import _publication_state_id, _source
from app.services.warehouse_relationship_service import _check
from app.warehouse.access import owned_workspace, owned_version, validate_sources
from app.warehouse.config import require_enabled
from app.warehouse.relationship_contract import WarehouseRelationship
from app.warehouse.sql_join_draft_planner import WarehouseJoinDraft
from app.warehouse.sql_scd2_draft_planner import Scd2SqlDraft
from app.warehouse.sql_physical_binding import bind_draft, InternalDraftBinding


def _pin(conn, workspace_id, user, *, model_id, version_id, dataset_id, state_id):
    model, version=owned_version(conn,workspace_id,model_id,version_id,user)
    if model["status"]!="APPROVED" or version["status"]!="APPROVED":
        raise HTTPException(409,"Warehouse model version is no longer approved")
    mappings=validate_sources(conn,workspace_id,version_id,user["owner_key"])
    primaries=[m for m in mappings if m["purpose"]=="PRIMARY"]
    if len(primaries)!=1 or primaries[0]["source_dataset_id"]!=dataset_id:
        raise HTTPException(409,"Exactly one matching approved PRIMARY source is required")
    rows=repository.metric_warehouse_bindings(conn,workspace_id,[dataset_id],"development")
    exact=[r for r in rows
           if r["warehouse_model_id"]==model_id
           and r["warehouse_model_version_id"]==version_id
           and _publication_state_id(r)==state_id]
    if len(exact)!=1:
        raise HTTPException(409,"Current publication is missing or ambiguous")
    return _source(exact[0],workspace_id,state_id)


def _relationship(conn,workspace_id,user,relationship_id):
    rows=conn.cursor(row_factory=dict_row).execute(
        """SELECT * FROM warehouse_model_relationships
           WHERE workspace_id=%s AND relationship_id=%s""",
        (workspace_id,relationship_id),
    ).fetchall()
    if len(rows)!=1:
        raise HTTPException(404,"Reviewed relationship not found in workspace")
    row=rows[0]
    contract=WarehouseRelationship.model_validate(
        {name:row[name] for name in WarehouseRelationship.model_fields}
    )
    _check(conn,contract,user)
    return contract


def resolve_join_draft(
    workspace_id: int,
    user: dict,
    draft: WarehouseJoinDraft | Scd2SqlDraft,
    *,
    relationship_id: int,
    fact_dataset_id: int,
    fact_state_id: int,
    dimension_dataset_id: int,
    dimension_state_id: int,
) -> InternalDraftBinding:
    """Build an INTERNAL binding; never return it from a public API.

    This does not verify a live BigQuery serving table and must not be used
    as an execution authorization. A future query gateway must revalidate
    everything at dispatch time, including physical provider evidence.
    """
    ids=(workspace_id,relationship_id,fact_dataset_id,fact_state_id,
         dimension_dataset_id,dimension_state_id)
    if any(type(i) is not int or i<1 for i in ids):
        raise ValueError("Positive workspace, relationship, dataset and state IDs are required")
    if type(draft) not in (WarehouseJoinDraft,Scd2SqlDraft) or draft.executable is not False:
        raise ValueError("An exact non-executable FACT/DIMENSION join draft is required")
    if (isinstance(draft,WarehouseJoinDraft) and draft.relationship_id!=relationship_id
            or isinstance(draft,Scd2SqlDraft) and draft.workspace_id!=workspace_id):
        raise ValueError("Join draft relationship/workspace pin mismatch")
    require_enabled()
    with repository_transaction() as conn:
        owned_workspace(conn,workspace_id,user)
        relation=_relationship(conn,workspace_id,user,relationship_id)
        expected_policy=("CURRENT_MEMBER" if isinstance(draft,WarehouseJoinDraft)
                         else "EVENT_TIME_AS_OF")
        if (relation.temporal_policy!=expected_policy
                or relation.fact_model_id!=draft.fact_model_id
                or relation.fact_model_version_id!=draft.fact_model_version_id
                or relation.dimension_model_id!=draft.dimension_model_id
                or relation.dimension_model_version_id!=draft.dimension_model_version_id):
            raise HTTPException(409,"Current reviewed relationship does not match the join draft")
        fact=_pin(conn,workspace_id,user,model_id=draft.fact_model_id,
                  version_id=draft.fact_model_version_id,dataset_id=fact_dataset_id,
                  state_id=fact_state_id)
        dimension=_pin(conn,workspace_id,user,model_id=draft.dimension_model_id,
                       version_id=draft.dimension_model_version_id,
                       dataset_id=dimension_dataset_id,state_id=dimension_state_id)
        if fact.model_type!="FACT" or dimension.model_type!="DIMENSION":
            raise HTTPException(409,"FACT/DIMENSION source types are inconsistent")
        return bind_draft(draft,fact=fact,dimension=dimension)
