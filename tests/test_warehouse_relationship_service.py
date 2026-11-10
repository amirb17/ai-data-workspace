"""Isolated PostgreSQL authorization, registration and stale-version behavior."""
from pathlib import Path
import pytest
from fastapi import HTTPException

from app.warehouse.relationship_contract import WarehouseRelationship
from app.services import warehouse_relationship_service as service
from app.services import warehouse_model_service as models
from app.schemas.warehouse import VersionCreate
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_relationship_postgres import approved_pair, MIGRATION

RELATION = {
    "workspace_id": 1,
    "fact_model_id": 1,
    "fact_model_version_id": 1,
    "dimension_model_id": 2,
    "dimension_model_version_id": 2,
    "name": "Customer at event",
    "key_pairs": [{"fact_column": "id", "dimension_column": "id"}],
    "temporal_policy": "EVENT_TIME_AS_OF",
    "fact_event_time_column": "effective",
}


def context(pg):
    ids = approved_pair(pg)
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
    fact, fv, dim, dv = ids
    return WarehouseRelationship.model_validate({
        **RELATION,
        "workspace_id": pg["workspace"],
        "fact_model_id": fact,
        "fact_model_version_id": fv,
        "dimension_model_id": dim,
        "dimension_model_version_id": dv,
    })


def test_relationship_registration_and_current_readback(modeling_pg):
    pg = modeling_pg
    relation = context(pg)
    receipt = service.register(pg["workspace"], pg["owner"], relation)
    assert receipt["relationship_id"] > 0
    rows = service.list_current(pg["workspace"], pg["owner"])
    assert len(rows) == 1
    assert rows[0]["relationship_id"] == receipt["relationship_id"]
    assert rows[0]["key_pairs"] == [{"fact_column": "id", "dimension_column": "id"}]
    assert rows[0]["temporal_policy"] == "EVENT_TIME_AS_OF"
    with pytest.raises(Exception):
        service.register(pg["workspace"], pg["owner"], relation)


def test_unowned_workspace_cannot_register_or_read(modeling_pg):
    pg = modeling_pg
    relation = context(pg)
    with pytest.raises(HTTPException) as exc:
        service.register(pg["workspace"], pg["other"], relation)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        service.list_current(pg["workspace"], pg["other"])
    assert exc.value.status_code == 403


def test_cross_workspace_payload_is_rejected(modeling_pg):
    pg = modeling_pg
    relation = context(pg)
    with pytest.raises(HTTPException) as exc:
        service.register(pg["foreign_workspace"], pg["owner"], relation)
    assert exc.value.status_code == 404


def test_outdated_version_relationship_is_hidden(modeling_pg):
    pg = modeling_pg
    relation = context(pg)
    service.register(pg["workspace"], pg["owner"], relation)
    new_version = models.create_version(
        pg["workspace"], relation.fact_model_id, pg["owner"], VersionCreate(expected_version=1)
    )
    assert new_version["status"] == "DRAFT"
    assert len(service.list_current(pg["workspace"], pg["owner"])) == 1
    # Existing approved version stays current until a new version is approved.
    # Deactivation of old joins is tested after the new version can be configured.
    with pg["connect"]() as conn:
        conn.execute(
            "UPDATE warehouse_models SET status='ARCHIVED', archived_at=NOW() WHERE warehouse_model_id=%s",
            (relation.fact_model_id,),
        )
    assert service.list_current(pg["workspace"], pg["owner"]) == []


def test_disabled_registry_never_opens_database(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_ENABLED", "false")
    monkeypatch.setattr(service, "repository_transaction", lambda: pytest.fail("Database accessed"))
    with pytest.raises(RuntimeError):
        service.list_current(1, {"owner_key": "usr:a"})
