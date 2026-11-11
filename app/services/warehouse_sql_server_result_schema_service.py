"""414F5: derive exact result columns from a persisted, approved model draft.

Backend-only. No SQL, column definitions or type hints from the read caller.
Does not authorize live provider IO or replace per-user access control.
"""
from dataclasses import dataclass

from app.db import warehouse_query_send_repository as repo
from app.schemas.warehouse_modeling import SourceSchema
from app.services.warehouse_sql_stored_draft_service import logical_draft_digest
from app.warehouse.sql_draft_planner import WarehouseSqlDraft, plan_draft

_TYPES = {
    "STRING": "STRING", "INTEGER": "INTEGER",
    "DECIMAL": "NUMERIC", "BOOLEAN": "BOOLEAN",
    "DATE": "DATE", "TIMESTAMP": "TIMESTAMP",
}


class ResultSchemaConflict(ValueError):
    pass


@dataclass(frozen=True)
class ServerResultSchema:
    attempt_id: int
    columns: tuple[tuple[str, str], ...]
    row_limit: int
    classification: str = "SERVER_PINNED_APPROVED_PROJECTION"
    provider_dispatched: bool = False


def inspect_server_result_schema(*, attempt_id: int) -> ServerResultSchema:
    """Recheck model approval, pins, logical draft and source types atomically."""
    if type(attempt_id) is not int or attempt_id < 1:
        raise ValueError("Server attempt ID required")
    with repo.transaction() as conn:
        c=repo.checkpoint(conn,attempt_id)
        repo.lock_identity(conn,c)
        a=repo.one(conn,"""
            SELECT logical_intent_sha256,workspace_id,owner_key
              FROM warehouse_query_attempt_registry
             WHERE attempt_id=%s
        """,(attempt_id,))
        d=repo.one(conn,"""
            SELECT workspace_id,owner_key,warehouse_model_id,
                   warehouse_model_version_id,normalized_sql,
                   projected_columns,row_limit
              FROM warehouse_query_attempt_logical_drafts
             WHERE attempt_id=%s
        """,(attempt_id,))
        if (not a or not d or d["workspace_id"] != c["workspace_id"]
                or d["owner_key"] != c["owner_key"]
                or a["workspace_id"] != c["workspace_id"]
                or a["owner_key"] != c["owner_key"]):
            raise ResultSchemaConflict("ORIGINAL_OWNED_DRAFT_REQUIRED")
        rows=conn.execute("""
            SELECT m.status,v.status,cfg.configuration,cfg.source_schema
            FROM warehouse_models m
            JOIN warehouse_model_versions v
              ON v.workspace_id=m.workspace_id
             AND v.warehouse_model_id=m.warehouse_model_id
            JOIN warehouse_model_configurations cfg
              ON cfg.workspace_id=v.workspace_id
             AND cfg.warehouse_model_id=v.warehouse_model_id
             AND cfg.warehouse_model_version_id=v.warehouse_model_version_id
            JOIN warehouse_query_attempt_publication_pins p
              ON p.attempt_id=%s
            JOIN warehouse_resources r ON r.resource_id=p.resource_id
            WHERE m.workspace_id=%s
              AND m.warehouse_model_id=%s
              AND v.warehouse_model_version_id=%s
              AND r.warehouse_model_id=m.warehouse_model_id
              AND r.warehouse_model_version_id=v.warehouse_model_version_id
        """,(attempt_id,c["workspace_id"],d["warehouse_model_id"],d["warehouse_model_version_id"])).fetchall()
        if len(rows)!=1 or rows[0][0]!="APPROVED" or rows[0][1]!="APPROVED":
            raise ResultSchemaConflict("PINNED_APPROVED_MODEL_REQUIRED")
        configuration,source=rows[0][2],rows[0][3]
        from app.schemas.warehouse_modeling import FactConfiguration, DimensionConfiguration
        if configuration.get("model_type")=="FACT":
            cfg=FactConfiguration.model_validate(configuration)
            names=(*cfg.grain.identity_columns,*cfg.measures,*cfg.attributes)
        elif configuration.get("model_type")=="DIMENSION":
            cfg=DimensionConfiguration.model_validate(configuration)
            names=(*cfg.business_keys,*cfg.tracked_attributes,*cfg.nonhistorical_attributes)
        else:
            raise ResultSchemaConflict("APPROVED_MODEL_TYPE_REQUIRED")
        if cfg.event_time is not None:
            names=(*names,cfg.event_time.column)
        allowed=set(names)
        schema=SourceSchema.model_validate(source)
        types={col.name:col.data_type for col in schema.columns}
        projected=d["projected_columns"]
        if (type(projected) is not list
                or not 1<=len(projected)<=32
                or len(set(projected))!=len(projected)
                or any(name not in allowed or types.get(name) not in _TYPES for name in projected)
                or type(d["row_limit"]) is not int
                or not 1<=d["row_limit"]<=500):
            raise ResultSchemaConflict("APPROVED_PROJECTION_AND_TYPES_REQUIRED")
        catalog={"workspace_id":c["workspace_id"],"models":[{
            "model_id":d["warehouse_model_id"],
            "model_version_id":d["warehouse_model_version_id"],
            "model_type":configuration["model_type"],
            "status":"APPROVED",
            "columns":[{"name":n,"role":"ATTRIBUTE"} for n in allowed],
        }]}
        try:
            planned=plan_draft(d["normalized_sql"],workspace_id=c["workspace_id"],catalog=catalog)
            exact=WarehouseSqlDraft(
                d["normalized_sql"],d["warehouse_model_id"],
                d["warehouse_model_version_id"],tuple(projected),d["row_limit"],
            )
            if (planned!=exact
                    or logical_draft_digest(exact)!=a["logical_intent_sha256"].strip()):
                raise ResultSchemaConflict("ORIGINAL_LOGICAL_DRAFT_FINGERPRINT_MISMATCH")
        except ResultSchemaConflict:
            raise
        except Exception:
            raise ResultSchemaConflict("ORIGINAL_LOGICAL_DRAFT_INVALID") from None
        return ServerResultSchema(
            attempt_id,tuple((name,_TYPES[types[name]]) for name in projected),d["row_limit"],
        )
