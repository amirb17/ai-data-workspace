"""Read-only PostgreSQL evidence for warehouse SQL eligibility.

An immutable PostgreSQL materialization receipt is *not* independent proof
that the physical BigQuery table remains readable. SQL stays disabled until a
separate provider verifier, read-only SQL guard and bounded runner exist.
"""
from fastapi import HTTPException
from psycopg.rows import dict_row

from app.db.database import repository_transaction
from app.warehouse.access import owned_workspace
from app.warehouse.config import require_enabled
from app.warehouse.query_eligibility import assess_eligibility


def _model_rows(conn, workspace_id, model_ids):
    return conn.cursor(row_factory=dict_row).execute(
        """
        SELECT m.warehouse_model_id AS model_id, m.status AS model_status,
               v.warehouse_model_version_id AS model_version_id,
               v.status AS version_status
        FROM warehouse_models m
        LEFT JOIN warehouse_model_versions v
          ON v.workspace_id=m.workspace_id
         AND v.warehouse_model_id=m.warehouse_model_id
         AND v.status='APPROVED'
        WHERE m.workspace_id=%s AND m.warehouse_model_id=ANY(%s)
        ORDER BY m.warehouse_model_id
        """, (workspace_id, list(model_ids)),
    ).fetchall()


def _sources_current(conn, workspace_id, version_id, owner_key):
    # No source is current if none exists, no PRIMARY exists, or ANY mapping
    # points at a foreign, missing, or archived dataset.
    rows=conn.cursor(row_factory=dict_row).execute(
        """
        SELECT s.purpose, d.dataset_id, d.workspace_id,
               d.owner, d.status
        FROM warehouse_model_sources s
        LEFT JOIN datasets d ON d.dataset_id=s.source_dataset_id
        WHERE s.workspace_id=%s AND s.warehouse_model_version_id=%s
        """, (workspace_id, version_id),
    ).fetchall()
    return (any(row["purpose"]=="PRIMARY" for row in rows)
            and all(row["dataset_id"] is not None
                    and row["workspace_id"]==workspace_id
                    and row["owner"]==owner_key
                    and row["status"]=="ACTIVE" for row in rows))


def _receipt_tables_available(conn):
    # Isolated tests and older installations may not yet have all phases.
    return all(conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None
               for name in ("warehouse_publications","warehouse_publication_heads",
                            "warehouse_serving_materializations"))


def _ledger_evidence(conn, workspace_id, model_id, version_id):
    """Return only exact current-head receipt matches. Never return physical refs."""
    rows=conn.cursor(row_factory=dict_row).execute(
        """
        SELECT r.resource_id,
               h.publication_id,
               h.revision AS publication_revision,
               p.publication_id AS matched_publication_id,
               sm.serving_materialization_id
        FROM warehouse_resources r
        LEFT JOIN warehouse_publication_heads h
          ON h.resource_id=r.resource_id
        LEFT JOIN warehouse_publications p
          ON p.resource_id=h.resource_id
         AND p.publication_id=h.publication_id
         AND p.revision=h.revision
        LEFT JOIN warehouse_serving_materializations sm
          ON sm.workspace_id=r.workspace_id
         AND sm.warehouse_model_id=r.warehouse_model_id
         AND sm.warehouse_model_version_id=r.warehouse_model_version_id
         AND sm.resource_id=r.resource_id
         AND sm.publication_id=h.publication_id
         AND sm.publication_revision=h.revision
         AND sm.generation=r.generation
        WHERE r.workspace_id=%s
          AND r.warehouse_model_id=%s
          AND r.warehouse_model_version_id=%s
          AND r.status='REGISTERED'
          AND r.provider='BIGQUERY'
        ORDER BY r.resource_id
        """, (workspace_id,model_id,version_id),
    ).fetchall()
    # Do not combine a head for one resource with a receipt for another.
    head_current=any(row["matched_publication_id"] is not None for row in rows)
    receipt_current=any(row["matched_publication_id"] is not None
                        and row["serving_materialization_id"] is not None
                        for row in rows)
    return head_current,receipt_current


def snapshot(workspace_id: int, user: dict, model_ids: list[int]) -> dict:
    """Inspect explicit model IDs under one authorized transaction.

    This diagnostic only supplies evidence and blocking reasons. No SQL plan
    is compiled and no provider calls or warehouse execution are possible.
    """
    require_enabled()
    if (not isinstance(model_ids,list) or not model_ids
            or len(model_ids)>16 or any(type(mid) is not int or mid<1 for mid in model_ids)
            or len(set(model_ids))!=len(model_ids)):
        raise ValueError("Select 1 to 16 distinct warehouse model IDs")
    with repository_transaction() as conn:
        owned_workspace(conn,workspace_id,user)
        rows=_model_rows(conn,workspace_id,model_ids)
        by_id={row["model_id"]:row for row in rows}
        available=_receipt_tables_available(conn)
        evidence=[]
        for model_id in model_ids:
            row=by_id.get(model_id)
            if row is None:
                raise HTTPException(404,"Warehouse model not found in workspace")
            approved=(row["model_status"]=="APPROVED"
                      and row["version_status"]=="APPROVED"
                      and row["model_version_id"] is not None)
            sources=(_sources_current(conn,workspace_id,row["model_version_id"],user["owner_key"])
                     if approved else False)
            publication=False
            receipt=False
            if approved and sources and available:
                publication,receipt=_ledger_evidence(
                    conn,workspace_id,model_id,row["model_version_id"])
            evidence.append({
                "workspace_id":workspace_id,
                "model_id":model_id,
                "model_version_id":row["model_version_id"],
                "status":"APPROVED" if approved else "UNAPPROVED",
                "sources_current":sources,
                "publication_current":publication,
                "serving_receipt_current":receipt,
                # A stored receipt is not an independent provider read.
                "serving_verified":False,
            })
    result=assess_eligibility(workspace_id=workspace_id,models=evidence,execution_enabled=False)
    return {
        "workspace_id":workspace_id,
        "models":evidence,
        "eligible":False,
        "blocked_reasons":[reason.value for reason in result.reasons],
        "query_execution_enabled":False,
        "provider_verification":"NOT_PERFORMED",
    }
