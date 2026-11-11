"""Read-only projection of approved warehouse models for the Models UI.

This intentionally does not infer fact/dimension joins or assert BigQuery readiness.
Registered resources and published serving receipts require separate verification.
"""
from collections import defaultdict

from app.db.database import repository_transaction
from app.warehouse.access import owned_workspace
from app.warehouse.config import require_enabled
from app.schemas.warehouse_modeling import ModelConfiguration, SourceSchema
from pydantic import TypeAdapter
from psycopg.rows import dict_row

_MODEL_ADAPTER = TypeAdapter(ModelConfiguration)


def _project(row, source_rows, resource_count):
    configuration = _MODEL_ADAPTER.validate_python(row["configuration"])
    schema = SourceSchema.model_validate(row["source_schema"])
    types = {column.name: column.data_type for column in schema.columns}

    if configuration.model_type == "FACT":
        keys = tuple(configuration.grain.identity_columns)
        measures = tuple(configuration.measures)
        attributes = tuple(configuration.attributes)
        time = configuration.event_time
        details = {
            "grain": configuration.grain.description,
            "scd_type": None,
        }
    else:
        keys = tuple(configuration.business_keys)
        measures = ()
        attributes = (
            tuple(configuration.tracked_attributes)
            + tuple(configuration.nonhistorical_attributes)
        )
        time = configuration.effective_time
        details = {
            "grain": None,
            "scd_type": configuration.scd_type,
        }

    roles = {}
    for name in keys:
        roles[name] = "IDENTITY"
    for name in measures:
        roles[name] = "MEASURE"
    for name in attributes:
        roles[name] = "ATTRIBUTE"
    if time is not None and time.column not in roles:
        roles[time.column] = "TIME"

    columns = [
        {"name": name, "type": types[name], "role": role}
        for name, role in roles.items()
    ]
    return {
        "model_id": row["warehouse_model_id"],
        "name": row["display_name"],
        "model_type": configuration.model_type,
        "model_version_id": row["warehouse_model_version_id"],
        "version_number": row["version_number"],
        "status": "APPROVED",
        "columns": columns,
        "identity_columns": list(keys),
        "measures": list(measures),
        "event_time_column": time.column if time is not None else None,
        **details,
        "sources": [
            {
                "dataset_id": source["source_dataset_id"],
                "dataset_name": source["dataset_name"],
                "purpose": source["purpose"],
            }
            for source in source_rows
        ],
        "resource_registered": resource_count > 0,
        # A registered resource is never proof of a published, queryable table.
        "serving_status": "NOT_VERIFIED",
        "queryable": False,
    }


def snapshot(workspace_id: int, user: dict) -> dict:
    """Read only immutable approved contracts in an authorized workspace.

    Existing access control uses SELECT FOR SHARE and therefore requires a
    regular transaction; this function issues no mutating statements.
    """
    require_enabled()
    with repository_transaction() as conn:
        owned_workspace(conn, workspace_id, user)

        cursor = conn.cursor(row_factory=dict_row)
        rows = cursor.execute(
            """
            SELECT m.warehouse_model_id, m.display_name,
                   v.warehouse_model_version_id, v.version_number,
                   c.configuration, c.source_schema
            FROM warehouse_models m
            JOIN warehouse_model_versions v
              ON v.workspace_id = m.workspace_id
             AND v.warehouse_model_id = m.warehouse_model_id
             AND v.status = 'APPROVED'
            JOIN warehouse_model_configurations c
              ON c.workspace_id = v.workspace_id
             AND c.warehouse_model_id = v.warehouse_model_id
             AND c.warehouse_model_version_id = v.warehouse_model_version_id
            WHERE m.workspace_id = %s AND m.status = 'APPROVED'
            ORDER BY m.warehouse_model_id
            """,
            (workspace_id,),
        ).fetchall()

        sources = cursor.execute(
            """
            SELECT s.warehouse_model_version_id,
                   s.source_dataset_id, s.purpose, d.dataset_name,
                   d.workspace_id AS dataset_workspace_id,
                   d.owner AS dataset_owner, d.status AS dataset_status
            FROM warehouse_model_sources s
            LEFT JOIN datasets d
              ON d.dataset_id = s.source_dataset_id
            WHERE s.workspace_id = %s
            ORDER BY s.warehouse_model_version_id, s.source_mapping_id
            """,
            (workspace_id,),
        ).fetchall()

        resource_rows = cursor.execute(
            """
            SELECT warehouse_model_version_id, COUNT(*) AS resource_count
            FROM warehouse_resources
            WHERE workspace_id = %s AND status = 'REGISTERED'
            GROUP BY warehouse_model_version_id
            """,
            (workspace_id,),
        ).fetchall()

    by_version = defaultdict(list)
    for source in sources:
        by_version[source["warehouse_model_version_id"]].append(source)
    counts = {r["warehouse_model_version_id"]: r["resource_count"] for r in resource_rows}
    models = []
    for row in rows:
        version_id = row["warehouse_model_version_id"]
        mappings = by_version[version_id]
        # Source membership must remain current; never show a partial contract.
        if (
            not any(m["purpose"] == "PRIMARY" for m in mappings)
            or any(
                m["dataset_workspace_id"] != workspace_id
                or m["dataset_owner"] != user["owner_key"]
                or m["dataset_status"] != "ACTIVE"
                for m in mappings
            )
        ):
            continue
        models.append(_project(row, mappings, counts.get(version_id, 0)))

    return {
        "workspace_id": workspace_id,
        "models": models,
        "readiness": {
            "status": "REVIEW_REQUIRED",
            "warehouse_sql_enabled": False,
            "deployment_authorized": False,
            "blockers": [
                {"code": "RUNTIME_ROLE_ACCEPTANCE",
                 "label": "Restricted PostgreSQL service-role acceptance pending"},
                {"code": "CLOUD_RETENTION_IAM",
                 "label": "BigQuery IAM and physical publication retention unverified"},
                {"code": "QUERY_EXECUTION_RELEASE",
                 "label": "Controlled BigQuery execution, recovery and billing not released"},
                {"code": "SERVING_READINESS",
                 "label": "Published warehouse serving metadata not independently verified"},
            ],
        },
        "relationships": [],
        "relationship_status": "NOT_MODELED",
        "query_execution_enabled": False,
    }
