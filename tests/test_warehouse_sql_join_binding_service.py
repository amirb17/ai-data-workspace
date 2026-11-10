"""Step 409C: reviewed relationship and independent publication pins."""
from contextlib import contextmanager
from dataclasses import replace

import pytest
from fastapi import HTTPException

from app.services import warehouse_sql_join_binding_service as service
from app.warehouse.sql_join_draft_planner import WarehouseJoinDraft
from app.warehouse.sql_scd2_draft_planner import Scd2SqlDraft
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_relationship_service import context
from app.services import warehouse_relationship_service as registry


def inputs(pg, relation_id, relation):
    return dict(workspace_id=pg["workspace"],user=pg["owner"],
        relationship_id=relation_id,
        fact_dataset_id=pg["dataset"],fact_state_id=1,
        dimension_dataset_id=pg["dataset"],dimension_state_id=1,
        draft=Scd2SqlDraft(
            normalized_sql="SELECT f.id FROM model_fact f JOIN model_dim d",
            workspace_id=pg["workspace"],
            fact_model_id=relation.fact_model_id,
            fact_model_version_id=relation.fact_model_version_id,
            dimension_model_id=relation.dimension_model_id,
            dimension_model_version_id=relation.dimension_model_version_id,
            projected_columns=(("f","id"),),row_limit=5,
        ))


def test_reviewed_relationship_requires_both_current_publications(modeling_pg):
    pg=modeling_pg
    relation=context(pg)
    receipt=registry.register(pg["workspace"],pg["owner"],relation)
    values=inputs(pg,receipt["relationship_id"],relation)
    with pytest.raises((ValueError,HTTPException)):
        service.resolve_join_draft(**values)


def test_unowned_user_cannot_read_reviewed_join(modeling_pg):
    pg=modeling_pg
    relation=context(pg)
    receipt=registry.register(pg["workspace"],pg["owner"],relation)
    values=inputs(pg,receipt["relationship_id"],relation)
    values["user"]=pg["other"]
    with pytest.raises(HTTPException) as caught:
        service.resolve_join_draft(**values)
    assert caught.value.status_code==403


def test_unknown_registry_relationship_cannot_be_inferred(modeling_pg):
    pg=modeling_pg
    relation=context(pg)
    values=inputs(pg,999999,relation)
    with pytest.raises(HTTPException) as caught:
        service.resolve_join_draft(**values)
    assert caught.value.status_code==404


def test_draft_policy_must_match_reviewed_relationship(modeling_pg):
    pg=modeling_pg
    relation=context(pg)
    receipt=registry.register(pg["workspace"],pg["owner"],relation)
    values=inputs(pg,receipt["relationship_id"],relation)
    values["draft"]=WarehouseJoinDraft(
        normalized_sql="SELECT f.id FROM model_fact AS f INNER JOIN model_dim AS d",
        relationship_id=receipt["relationship_id"],
        fact_model_id=relation.fact_model_id,
        fact_model_version_id=relation.fact_model_version_id,
        dimension_model_id=relation.dimension_model_id,
        dimension_model_version_id=relation.dimension_model_version_id,
        projected_columns=(("f","id"),),row_limit=5,
    )
    with pytest.raises(HTTPException) as caught:
        service.resolve_join_draft(**values)
    assert caught.value.status_code==409


@pytest.mark.parametrize("field",[
    "workspace_id","relationship_id","fact_dataset_id","fact_state_id",
    "dimension_dataset_id","dimension_state_id",
])
def test_invalid_ids_rejected_before_database(field,monkeypatch):
    monkeypatch.setattr(service,"repository_transaction",
                        lambda:pytest.fail("Invalid input entered database"))
    values=dict(workspace_id=1,user={"owner_key":"test"},relationship_id=2,
                fact_dataset_id=3,fact_state_id=4,dimension_dataset_id=5,
                dimension_state_id=6,
                draft=WarehouseJoinDraft("SELECT 1",2,10,11,20,21,(("f","id"),),5))
    values[field]=True
    with pytest.raises(ValueError):
        service.resolve_join_draft(**values)


def test_no_provider_or_execution_imports():
    import ast
    from pathlib import Path
    path=Path(__file__).parents[1]/"app/services/warehouse_sql_join_binding_service.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(name and name.startswith(("google","boto3","app.warehouse.providers.bigquery"))
                   for name in imports)
