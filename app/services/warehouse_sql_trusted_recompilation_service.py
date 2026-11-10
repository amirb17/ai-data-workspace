"""414D4d: owned one-model SQL recompile from PostgreSQL publication evidence.

A strict, conservative current-head path. It never accepts caller-supplied SQL,
physical table, column mapping, provider client, compiler evidence, or token.
No claim/execute/authorization grant is possible from this result.
"""
from dataclasses import dataclass
from psycopg.rows import dict_row

from app.db.database import repository_transaction
from app.db import warehouse_query_attempt_repository as attempts
from app.db import warehouse_repository
from app.services.metric_warehouse_binding_service import (
    _publication_state_id, _source,
)
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_stored_draft_service import (
    _catalog_for_owned_model, _matches, logical_draft_digest,
)
from app.warehouse.sql_draft_planner import plan_draft
from app.warehouse.sql_physical_binding import bind_draft
from app.warehouse.sql_single_model_compiler import compile_single_model_sql
from app.db.warehouse_query_publication_inspection_repository import (
    inspect_linked_publications,
)


@dataclass(frozen=True)
class StoredWarehouseCompilation:
    attempt_id: int
    reservation_id: int
    compiled_intent_sha256: str
    physical_scope_verified: bool
    reason: str = "VERIFIED_INTERNAL_NON_EXECUTING"
    execution_authorized: bool = False
    reservation_claimed: bool = False
    provider_dispatched: bool = False


def recompile_stored_warehouse_attempt_without_dispatch(
    *, governance: QueryGovernanceService, attempt_handle: str,
) -> StoredWarehouseCompilation:
    """Resolve server-owned logical+physical state and compare reserved intent.

    Deliberately rejects old-head links for this phase. Historical snapshot
    reads require a separate, explicit retention/authorization protocol.
    """
    if type(governance) is not QueryGovernanceService:
        raise ValueError("Trusted governance context required")

    with repository_transaction() as conn:
        result, _ = _recompile_in_transaction(conn, governance, attempt_handle)
        return result


def _recompile_in_transaction(conn, governance, attempt_handle):
    """Trusted internal composition; callers own transaction and identity locks."""
    attempt=attempts.read_attempt(
        conn, workspace_id=governance.workspace_id,
        owner_key=governance.owner_key, attempt_handle=attempt_handle,
    )
    inspected=inspect_linked_publications(
        conn,workspace_id=governance.workspace_id,
        owner_key=governance.owner_key,attempt_id=attempt["attempt_id"],
    )
    if not inspected.metadata_consistent:
        raise ValueError("WAREHOUSE_QUERY_DEPENDENCY_NOT_READY")
    stored=conn.cursor(row_factory=dict_row).execute(
        """SELECT * FROM warehouse_query_attempt_logical_drafts
           WHERE attempt_id=%s FOR SHARE""",
        (attempt["attempt_id"],),
    ).fetchone()
    if not stored:
        raise ValueError("WAREHOUSE_LOGICAL_DRAFT_NOT_REGISTERED")
    catalog=_catalog_for_owned_model(
        conn,governance.workspace_id,governance.owner_key,
        governance.user,stored["warehouse_model_id"],
    )
    draft=plan_draft(stored["normalized_sql"],
                     workspace_id=governance.workspace_id,catalog=catalog)
    if (not _matches(stored,draft)
            or logical_draft_digest(draft)!=attempt["logical_intent_sha256"].strip()):
        raise ValueError("WAREHOUSE_LOGICAL_DRAFT_REVALIDATION_FAILED")
    if len(inspected.publication_ids)!=1:
        raise ValueError("ONE_MODEL_PUBLICATION_SCOPE_REQUIRED")

    pins=conn.cursor(row_factory=dict_row).execute(
        """SELECT p.resource_id,p.publication_id,p.publication_revision,
                  r.warehouse_model_id,r.warehouse_model_version_id
           FROM warehouse_query_attempt_publication_pins p
           JOIN warehouse_resources r ON r.resource_id=p.resource_id
           WHERE p.attempt_id=%s FOR SHARE OF p,r""",
        (attempt["attempt_id"],),
    ).fetchall()
    if len(pins)!=1 or (pins[0]["warehouse_model_id"],
                       pins[0]["warehouse_model_version_id"])!=(
                       draft.model_id,draft.model_version_id):
        raise ValueError("WAREHOUSE_QUERY_PIN_MODEL_MISMATCH")
    pin=pins[0]

    # Existing authoritative resolver validates provider-generated table
    # naming, source state, configuration/schema/source-vector checksums,
    # serving receipt materialization identity and typed column mapping.
    rows=warehouse_repository.metric_warehouse_bindings(
        conn,governance.workspace_id,
        [s["dataset_id"] for s in catalog["models"][0]["sources"]
         if s["purpose"]=="PRIMARY"],
        environment="development",
    )
    exact=[row for row in rows if
           row["resource_id"]==pin["resource_id"]
           and row["publication_id"]==pin["publication_id"]
           and row["publication_revision"]==pin["publication_revision"]
           and row["warehouse_model_id"]==draft.model_id
           and row["warehouse_model_version_id"]==draft.model_version_id]
    if len(exact)!=1:
        raise ValueError("WAREHOUSE_CURRENT_SERVING_PUBLICATION_REQUIRED")
    row=exact[0]
    state_id=_publication_state_id(row)
    source=_source(row,governance.workspace_id,state_id)
    binding=bind_draft(draft,fact=source)
    generated=compile_single_model_sql(
        workspace_id=governance.workspace_id,draft=draft,binding=binding,
    )
    reservation=conn.cursor(row_factory=dict_row).execute(
        """SELECT request_token,intent_sha256,status,workspace_id,owner_key,
                  consumer,provider FROM query_governance_reservations
           WHERE reservation_id=%s FOR SHARE""",
        (inspected.reservation_id,),
    ).fetchone()
    if (not reservation
            or reservation["workspace_id"]!=governance.workspace_id
            or reservation["owner_key"]!=governance.owner_key
            or reservation["consumer"]!="SQL"
            or reservation["provider"]!="BIGQUERY"
            or reservation["status"] not in ("ACTIVE","EXECUTING")
            or reservation["request_token"]!=attempt["request_token"].strip()
            or reservation["intent_sha256"]!=generated.intent_sha256):
        raise ValueError("WAREHOUSE_COMPILED_RESERVATION_INTENT_MISMATCH")

    return StoredWarehouseCompilation(
        attempt_id=attempt["attempt_id"],
        reservation_id=inspected.reservation_id,
        compiled_intent_sha256=generated.intent_sha256,
        physical_scope_verified=True,
    ), generated
