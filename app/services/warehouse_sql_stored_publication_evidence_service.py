"""414D4c: resolve real publication receipt for a stored logical draft.

Uses only PostgreSQL state, not supplied physical table names, compiler
objects or provider clients. Positive evidence is NOT an execution permit.
"""
from dataclasses import dataclass

from psycopg.rows import dict_row

from app.db.database import repository_transaction
from app.db import warehouse_query_attempt_repository as attempts
from app.db.warehouse_query_publication_inspection_repository import (
    inspect_linked_publications,
)
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_stored_draft_service import (
    _catalog_for_owned_model,logical_draft_digest,_matches,
)
from app.warehouse.sql_draft_planner import plan_draft


@dataclass(frozen=True)
class ResolvedPublishedDraftEvidence:
    attempt_id: int
    reservation_id: int
    model_id: int
    model_version_id: int
    resource_id: int
    publication_id: int
    publication_revision: int
    generation: int
    serving_materialization_id: int
    schema_sha256: str
    serving_sha256: str
    normalized_logical_sql: str
    execution_authorized: bool = False
    provider_dispatched: bool = False


def resolve_stored_publication_evidence_without_dispatch(
    *, governance: QueryGovernanceService, attempt_handle: str,
) -> ResolvedPublishedDraftEvidence:
    """Reconstruct one model's receipt from owned immutable PostgreSQL rows."""
    if type(governance) is not QueryGovernanceService:
        raise ValueError("Trusted governance context required")
    with repository_transaction() as conn:
        original=attempts.read_attempt(
            conn,workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,attempt_handle=attempt_handle,
        )
        inspection=inspect_linked_publications(
            conn,workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,attempt_id=original["attempt_id"],
        )
        if not inspection.metadata_consistent:
            raise ValueError("WAREHOUSE_PUBLICATION_DEPENDENCY_NOT_READY")
        stored=conn.cursor(row_factory=dict_row).execute(
            """SELECT * FROM warehouse_query_attempt_logical_drafts
               WHERE attempt_id=%s FOR SHARE""",
            (original["attempt_id"],),
        ).fetchone()
        if stored is None:
            raise ValueError("WAREHOUSE_LOGICAL_DRAFT_NOT_REGISTERED")
        catalog=_catalog_for_owned_model(
            conn,governance.workspace_id,governance.owner_key,
            governance.user,stored["warehouse_model_id"],
        )
        draft=plan_draft(
            stored["normalized_sql"],
            workspace_id=governance.workspace_id,catalog=catalog,
        )
        if (not _matches(stored,draft)
                or logical_draft_digest(draft)!=original["logical_intent_sha256"].strip()):
            raise ValueError("WAREHOUSE_LOGICAL_DRAFT_REVALIDATION_FAILED")

        row=conn.cursor(row_factory=dict_row).execute(
            """SELECT r.resource_id,r.warehouse_model_id,
                      r.warehouse_model_version_id,r.generation,
                      p.publication_id,p.publication_revision,
                      sm.serving_materialization_id,sm.schema_sha256,
                      sm.serving_sha256
               FROM warehouse_query_attempt_publication_pins p
               JOIN warehouse_resources r ON r.resource_id=p.resource_id
               JOIN warehouse_serving_materializations sm
                 ON sm.resource_id=p.resource_id
                AND sm.publication_id=p.publication_id
                AND sm.publication_revision=p.publication_revision
                AND sm.workspace_id=r.workspace_id
                AND sm.warehouse_model_id=r.warehouse_model_id
                AND sm.warehouse_model_version_id=r.warehouse_model_version_id
                AND sm.generation=r.generation
               WHERE p.attempt_id=%s
                 AND r.workspace_id=%s
                 AND r.status='REGISTERED'
                 AND r.environment='development'
                 AND r.provider='BIGQUERY'
               FOR SHARE OF p,r,sm""",
            (original["attempt_id"],governance.workspace_id),
        ).fetchall()
        if (len(row)!=1 or len(inspection.publication_ids)!=1
                or row[0]["warehouse_model_id"]!=draft.model_id
                or row[0]["warehouse_model_version_id"]!=draft.model_version_id):
            raise ValueError("WAREHOUSE_SERVING_RECEIPT_NOT_READY")
        record=row[0]
        return ResolvedPublishedDraftEvidence(
            attempt_id=original["attempt_id"],
            reservation_id=inspection.reservation_id,
            model_id=record["warehouse_model_id"],
            model_version_id=record["warehouse_model_version_id"],
            resource_id=record["resource_id"],
            publication_id=record["publication_id"],
            publication_revision=record["publication_revision"],
            generation=record["generation"],
            serving_materialization_id=record["serving_materialization_id"],
            schema_sha256=record["schema_sha256"],
            serving_sha256=record["serving_sha256"],
            normalized_logical_sql=draft.normalized_sql,
        )
