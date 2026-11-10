"""Scoped registration and readback for explicitly reviewed warehouse joins.

No inference, cloud access, BigQuery query, or pipeline state changes.
"""
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from app.db.database import repository_transaction
from app.db import warehouse_repository as repository
from app.schemas.warehouse_modeling import ModelConfiguration, SourceSchema
from app.warehouse.access import owned_workspace, owned_version, validate_sources
from app.warehouse.config import require_enabled
from app.warehouse.relationship_contract import WarehouseRelationship, validate_relationship

_ADAPTER = TypeAdapter(ModelConfiguration)


def _version_context(conn, workspace_id, model_id, version_id, user, expected):
    model, version = owned_version(conn, workspace_id, model_id, version_id, user)
    if model["status"] != "APPROVED" or version["status"] != "APPROVED":
        raise HTTPException(409, "Relationship requires currently approved model versions")
    mappings = validate_sources(conn, workspace_id, version_id, user["owner_key"])
    if not any(mapping["purpose"] == "PRIMARY" for mapping in mappings):
        raise HTTPException(409, "Relationship source membership incomplete")
    record = repository.modeling(conn, workspace_id, model_id, version_id)
    if not record or record["model_type"] != expected:
        raise HTTPException(409, "Relationship requires explicit typed model configuration")
    config = _ADAPTER.validate_python(record["configuration"])
    schema = SourceSchema.model_validate(record["source_schema"])
    return config, schema


def _check(conn, relation, user):
    fact, fact_schema = _version_context(
        conn, relation.workspace_id, relation.fact_model_id,
        relation.fact_model_version_id, user, "FACT"
    )
    dimension, dimension_schema = _version_context(
        conn, relation.workspace_id, relation.dimension_model_id,
        relation.dimension_model_version_id, user, "DIMENSION"
    )
    validate_relationship(relation, fact, fact_schema, dimension, dimension_schema)


def register(workspace_id: int, user: dict, relation: WarehouseRelationship):
    require_enabled()
    if relation.workspace_id != workspace_id:
        raise HTTPException(404, "Relationship workspace mismatch")
    with repository_transaction() as conn:
        owned_workspace(conn, workspace_id, user, mutate=True)
        _check(conn, relation, user)
        row = conn.cursor(row_factory=dict_row).execute(
            """
            INSERT INTO warehouse_model_relationships (
                workspace_id, fact_model_id, fact_model_version_id,
                dimension_model_id, dimension_model_version_id,
                name, cardinality, temporal_policy,
                fact_event_time_column, key_pairs, reviewed_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING relationship_id, reviewed_at
            """,
            (
                relation.workspace_id, relation.fact_model_id,
                relation.fact_model_version_id, relation.dimension_model_id,
                relation.dimension_model_version_id, relation.name,
                relation.cardinality, relation.temporal_policy,
                relation.fact_event_time_column,
                Jsonb([pair.model_dump() for pair in relation.key_pairs]),
                user["user_id"],
            ),
        ).fetchone()
        return {
            "relationship_id": row["relationship_id"],
            "reviewed_at": row["reviewed_at"],
            "relationship": relation.model_dump(mode="json"),
        }


def list_current(workspace_id: int, user: dict):
    """Return only reviewed edges that remain valid in current approved versions."""
    require_enabled()
    with repository_transaction() as conn:
        owned_workspace(conn, workspace_id, user)
        rows = conn.cursor(row_factory=dict_row).execute(
            """
            SELECT r.*
            FROM warehouse_model_relationships r
            WHERE r.workspace_id = %s
            ORDER BY r.relationship_id
            """,
            (workspace_id,),
        ).fetchall()
        verified = []
        for row in rows:
            relation = WarehouseRelationship.model_validate({
                **{field: row[field] for field in WarehouseRelationship.model_fields},
                "schema_version": row["schema_version"],
            })
            try:
                _check(conn, relation, user)
            except (HTTPException, ValueError):
                continue
            verified.append({
                "relationship_id": row["relationship_id"],
                "reviewed_at": row["reviewed_at"].isoformat(),
                **relation.model_dump(mode="json"),
            })
        return verified
