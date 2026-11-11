"""414D4a: persisted owned logical SQL draft, no physical query execution.

The initial registration accepts reviewed LOGICAL SQL only, reparses it with
the existing strict planner and owned approved catalog, and binds it to one
immutable server-issued attempt. The later resolver takes only the attempt
handle, never SQL or physical binding inputs. This is not dispatch approval.
"""
from dataclasses import dataclass
import hashlib
import json

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.db.database import repository_transaction
from app.db import warehouse_query_attempt_repository as attempts
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_catalog_service import _project
from app.warehouse.access import owned_workspace
from app.warehouse.sql_draft_planner import WarehouseSqlDraft, plan_draft


@dataclass(frozen=True)
class StoredWarehouseLogicalDraft:
    attempt_id: int
    draft: WarehouseSqlDraft
    persisted: bool
    execution_authorized: bool = False
    provider_dispatched: bool = False


def logical_draft_digest(draft: WarehouseSqlDraft) -> str:
    """Deterministic logical identity only, not a provider/compiler intent."""
    if type(draft) is not WarehouseSqlDraft or draft.executable is not False:
        raise ValueError("Reviewed bounded logical draft required")
    payload=json.dumps({
        "domain":"warehouse-logical-draft-v1",
        "model_id":draft.model_id,"version_id":draft.model_version_id,
        "sql":draft.normalized_sql,
        "columns":list(draft.projected_columns),"limit":draft.row_limit,
    },sort_keys=True,separators=(",",":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _catalog_for_owned_model(conn, workspace_id, owner_key, user, model_id):
    owned_workspace(conn,workspace_id,user)
    row=conn.cursor(row_factory=dict_row).execute(
        """SELECT m.warehouse_model_id,m.display_name,
                  v.warehouse_model_version_id,v.version_number,
                  c.configuration,c.source_schema
           FROM warehouse_models m
           JOIN warehouse_model_versions v
             ON v.workspace_id=m.workspace_id
            AND v.warehouse_model_id=m.warehouse_model_id
           JOIN warehouse_model_configurations c
             ON c.workspace_id=v.workspace_id
            AND c.warehouse_model_id=v.warehouse_model_id
            AND c.warehouse_model_version_id=v.warehouse_model_version_id
           WHERE m.workspace_id=%s AND m.warehouse_model_id=%s
             AND m.status='APPROVED' AND v.status='APPROVED'
           FOR SHARE OF m,v,c""",
        (workspace_id,model_id),
    ).fetchone()
    if row is None:
        raise ValueError("OWNED_APPROVED_MODEL_NOT_FOUND")
    sources=conn.cursor(row_factory=dict_row).execute(
        """SELECT s.source_dataset_id,s.purpose,d.dataset_name,
                  d.workspace_id AS dataset_workspace_id,
                  d.owner AS dataset_owner,d.status AS dataset_status
           FROM warehouse_model_sources s
           LEFT JOIN datasets d ON d.dataset_id=s.source_dataset_id
           WHERE s.workspace_id=%s
             AND s.warehouse_model_version_id=%s
           ORDER BY s.source_mapping_id""",
        (workspace_id,row["warehouse_model_version_id"]),
    ).fetchall()
    if (not any(s["purpose"]=="PRIMARY" for s in sources)
            or any(s["dataset_workspace_id"]!=workspace_id
                   or s["dataset_owner"]!=owner_key
                   or s["dataset_status"]!="ACTIVE" for s in sources)):
        raise ValueError("WAREHOUSE_SOURCE_MEMBERSHIP_REVOKED")
    return {"workspace_id":workspace_id,
            "models":[_project(row,sources,0)]}


def _load_attempt(conn, governance, handle):
    return attempts.read_attempt(
        conn,workspace_id=governance.workspace_id,
        owner_key=governance.owner_key,attempt_handle=handle,
    )


def register_reviewed_logical_draft_without_dispatch(
    *, governance: QueryGovernanceService, attempt_handle: str,
    model_id: int, logical_sql: str,
) -> StoredWarehouseLogicalDraft:
    """Backend registration phase only; client SQL must never reach dispatch."""
    if type(governance) is not QueryGovernanceService:
        raise ValueError("Governance principal required")
    if type(model_id) is not int or model_id < 1:
        raise ValueError("Positive approved warehouse model required")
    with repository_transaction() as conn:
        return _register_in_transaction(conn, governance, attempt_handle, model_id, logical_sql)


def _register_in_transaction(conn, governance, attempt_handle, model_id, logical_sql):
    """Internal composition; caller owns the authenticated admission transaction."""
    original=_load_attempt(conn,governance,attempt_handle)
    catalog=_catalog_for_owned_model(
        conn,governance.workspace_id,governance.owner_key,
        governance.user,model_id,
    )
    draft=plan_draft(
        logical_sql,workspace_id=governance.workspace_id,catalog=catalog,
    )
    if draft.model_id!=model_id:
        raise ValueError("LOGICAL_DRAFT_MODEL_MISMATCH")
    if logical_draft_digest(draft)!=original["logical_intent_sha256"].strip():
        raise ValueError("LOGICAL_DRAFT_ATTEMPT_DIGEST_MISMATCH")
    saved=conn.cursor(row_factory=dict_row).execute(
        """SELECT * FROM warehouse_query_attempt_logical_drafts
           WHERE attempt_id=%s FOR SHARE""",
        (original["attempt_id"],),
    ).fetchone()
    if saved:
        if _matches(saved,draft):
            return StoredWarehouseLogicalDraft(
                original["attempt_id"],draft,True,
            )
        raise ValueError("LOGICAL_DRAFT_IMMUTABLE_CONFLICT")
    conn.execute(
        """INSERT INTO warehouse_query_attempt_logical_drafts(
            attempt_id,workspace_id,owner_key,warehouse_model_id,
            warehouse_model_version_id,normalized_sql,projected_columns,
            row_limit
           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (original["attempt_id"],governance.workspace_id,
         governance.owner_key,draft.model_id,draft.model_version_id,
         draft.normalized_sql,Jsonb(list(draft.projected_columns)),
         draft.row_limit),
    )
    return StoredWarehouseLogicalDraft(original["attempt_id"],draft,True)



def _matches(row,draft):
    return (
        row["warehouse_model_id"]==draft.model_id
        and row["warehouse_model_version_id"]==draft.model_version_id
        and row["normalized_sql"]==draft.normalized_sql
        and row["projected_columns"]==list(draft.projected_columns)
        and row["row_limit"]==draft.row_limit
    )


def resolve_reviewed_logical_draft_without_dispatch(
    *, governance: QueryGovernanceService, attempt_handle: str,
) -> StoredWarehouseLogicalDraft:
    """Only server-owned attempt handle is supplied; replan stored logical SQL."""
    if type(governance) is not QueryGovernanceService:
        raise ValueError("Governance principal required")
    with repository_transaction() as conn:
        original=_load_attempt(conn,governance,attempt_handle)
        saved=conn.cursor(row_factory=dict_row).execute(
            """SELECT * FROM warehouse_query_attempt_logical_drafts
               WHERE attempt_id=%s FOR SHARE""",
            (original["attempt_id"],),
        ).fetchone()
        if saved is None:
            raise ValueError("WAREHOUSE_LOGICAL_DRAFT_NOT_REGISTERED")
        if (saved["workspace_id"]!=governance.workspace_id
                or saved["owner_key"]!=governance.owner_key):
            raise ValueError("WAREHOUSE_LOGICAL_DRAFT_NOT_OWNED")
        catalog=_catalog_for_owned_model(
            conn,governance.workspace_id,governance.owner_key,
            governance.user,saved["warehouse_model_id"],
        )
        draft=plan_draft(
            saved["normalized_sql"],workspace_id=governance.workspace_id,
            catalog=catalog,
        )
        if (not _matches(saved,draft)
                or logical_draft_digest(draft)!=original[
                    "logical_intent_sha256"].strip()):
            raise ValueError("WAREHOUSE_LOGICAL_DRAFT_REVALIDATION_FAILED")
        return StoredWarehouseLogicalDraft(original["attempt_id"],draft,True)
