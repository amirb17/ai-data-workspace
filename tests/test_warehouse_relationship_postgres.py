"""Migration 0048: immutable, scoped, reviewed FACT/DIMENSION relationships."""
from pathlib import Path
import pytest
import psycopg
from psycopg.types.json import Jsonb

from app.schemas.warehouse import SourceMapping
from app.schemas.warehouse_modeling import ModelingRequest
from app.services import warehouse_model_service as models
from app.services import warehouse_modeling_service as modeling
from tests.test_warehouse_modeling import modeling_pg, configured, dimension

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/0048_warehouse_relationships.sql"


def approved_pair(pg):
    fact = {
        "model_type": "FACT",
        "grain": {"description": "One line", "identity_columns": ["line"]},
        "attributes": ["id"],
        "event_time": {"column": "effective", "interpretation": "UTC"},
    }
    fact_id, fact_version, _, _ = configured(pg, fact, "Fact order lines")
    dim_id, dim_version, _, _ = configured(pg, dimension(), "Dimension customers")
    models.approve_version(pg["workspace"], fact_id, fact_version, pg["owner"])
    models.approve_version(pg["workspace"], dim_id, dim_version, pg["owner"])
    return fact_id, fact_version, dim_id, dim_version


def insert(conn, pg, ids, **overrides):
    fact_id, fact_version, dim_id, dim_version = ids
    fields = {
        "workspace_id": pg["workspace"],
        "fact_model_id": fact_id,
        "fact_model_version_id": fact_version,
        "dimension_model_id": dim_id,
        "dimension_model_version_id": dim_version,
        "name": "Customer at event",
        "cardinality": "MANY_TO_ONE",
        "temporal_policy": "EVENT_TIME_AS_OF",
        "fact_event_time_column": "effective",
        "key_pairs": [{"fact_column": "id", "dimension_column": "id"}],
        "reviewed_by": pg["owner"]["user_id"],
    }
    fields.update(overrides)
    return conn.execute(
        """INSERT INTO warehouse_model_relationships (
            workspace_id, fact_model_id, fact_model_version_id,
            dimension_model_id, dimension_model_version_id, name, cardinality,
            temporal_policy, fact_event_time_column, key_pairs, reviewed_by
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING relationship_id""",
        (fields["workspace_id"],fields["fact_model_id"],
         fields["fact_model_version_id"],fields["dimension_model_id"],
         fields["dimension_model_version_id"],fields["name"],
         fields["cardinality"],fields["temporal_policy"],
         fields["fact_event_time_column"],Jsonb(fields["key_pairs"]),
         fields["reviewed_by"]),
    ).fetchone()[0]


def test_additive_relationship_migration_and_immutability(modeling_pg):
    pg = modeling_pg
    ids = approved_pair(pg)
    with pg["connect"]() as conn:
        before = conn.execute("SELECT count(*) FROM warehouse_models").fetchone()[0]
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
        assert conn.execute("SELECT count(*) FROM warehouse_models").fetchone()[0] == before
        relationship_id = insert(conn, pg, ids)
        assert relationship_id > 0
    for operation in (
        "UPDATE warehouse_model_relationships SET name=name",
        "DELETE FROM warehouse_model_relationships",
        "TRUNCATE warehouse_model_relationships",
    ):
        with pytest.raises(psycopg.Error), pg["connect"]() as conn:
            conn.execute(operation)
    with pytest.raises(psycopg.Error), pg["connect"]() as conn:
        insert(conn, pg, ids)  # Reviewed identity must not duplicate.


@pytest.mark.parametrize("override", [
    {"workspace_id": 99999},
    {"fact_model_version_id": 99999},
    {"key_pairs": [{"fact_column": "line", "dimension_column": "id"}]},
    {"key_pairs": [{"fact_column": "unknown", "dimension_column": "id"}]},
    {"key_pairs": [{"fact_column": "id", "dimension_column": "unknown"}]},
    {"temporal_policy": "CURRENT_MEMBER", "fact_event_time_column": None},
    {"fact_event_time_column": "line"},
    {"key_pairs": [{"fact_column": "id", "dimension_column": "id", "physical_table": "bad"}]},
])
def test_database_fails_closed_on_unreviewed_join_semantics(modeling_pg, override):
    pg = modeling_pg
    ids = approved_pair(pg)
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
    with pytest.raises(psycopg.Error), pg["connect"]() as conn:
        insert(conn, pg, ids, **override)
    with pg["connect"]() as conn:
        assert conn.execute("SELECT count(*) FROM warehouse_model_relationships").fetchone()[0] == 0
