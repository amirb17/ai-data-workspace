"""Step 409B: exact PostgreSQL publication binding in disposable schemas.

Uses fake BigQuery providers only; never submits a warehouse SQL query.
"""
from datetime import timedelta
import pytest
from fastapi import HTTPException

from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_provider_postgres import fact_provider_pg, provider_pg
from tests.test_metric_warehouse_binding_postgres import publish_warehouse
from tests.test_warehouse_serving_postgres import publication, row
from tests.test_warehouse_reliability import NOW

from app.warehouse.sql_draft_planner import WarehouseSqlDraft
from app.services.warehouse_sql_binding_service import resolve_single_draft


def draft(f, column="id"):
    return WarehouseSqlDraft(
        normalized_sql=f"SELECT {column} FROM model_{f.model} LIMIT 5",
        model_id=f.model,
        model_version_id=f.version,
        projected_columns=(column,),
        row_limit=5,
    )


def resolve(f, state_id, owner=None, *, sql_draft=None, dataset_id=None):
    return resolve_single_draft(
        f.pg["workspace"],f.pg["owner"] if owner is None else owner,
        draft(f) if sql_draft is None else sql_draft,
        source_dataset_id=f.pg["dataset"] if dataset_id is None else dataset_id,
        source_state_id=state_id,
    )


@pytest.mark.parametrize("fixture_name", ["fact_provider_pg","provider_pg"])
def test_exact_current_publication_resolves_internal_nonexecutable_binding(fixture_name,request):
    f=request.getfixturevalue(fixture_name)
    logical,published,state_id=publish_warehouse(f)
    with pytest.raises(ValueError):
        resolve(f,state_id)  # Publication alone is not a serving receipt.
    receipt=f.provider.materialize_current_publication(f.resource)
    resolved=resolve(f,state_id)
    assert resolved.executable is False
    assert len(resolved.models)==1
    pin=resolved.models[0]
    assert (pin.model_id,pin.model_version_id)==(f.model,f.version)
    assert (pin.publication_id,pin.publication_revision)==(
        published["publication_id"],published["revision"])
    assert pin.serving_materialization_id==receipt["serving_materialization_id"]
    assert pin.physical_table.startswith("synthetic-project.dr_dev_w")
    assert resolved.columns[0].logical_name=="id"
    assert resolved.columns[0].physical_name.startswith("c")


def test_cross_owner_cannot_resolve_private_warehouse_publication(fact_provider_pg):
    f=fact_provider_pg
    logical,published,state_id=publish_warehouse(f)
    f.provider.materialize_current_publication(f.resource)
    with pytest.raises(HTTPException) as exc:
        resolve(f,state_id,owner=f.pg["other"])
    assert exc.value.status_code==403


def test_model_version_and_primary_dataset_must_match(fact_provider_pg):
    f=fact_provider_pg
    logical,published,state_id=publish_warehouse(f)
    f.provider.materialize_current_publication(f.resource)
    from dataclasses import replace
    with pytest.raises((HTTPException,ValueError)):
        resolve(f,state_id,sql_draft=replace(draft(f),model_version_id=f.version+1))
    with pytest.raises((HTTPException,ValueError)):
        resolve(f,state_id,dataset_id=f.pg["dataset"]+100000)


def test_old_receipt_cannot_resolve_new_publication(fact_provider_pg):
    f=fact_provider_pg
    logical,published,old_state=publish_warehouse(f)
    f.provider.materialize_current_publication(f.resource)
    assert resolve(f,old_state).executable is False
    later=NOW+timedelta(days=180)
    publication(f,[row("B",at=later)],key="second",at=later)
    with pytest.raises((HTTPException,ValueError)):
        resolve(f,old_state)
    with pytest.raises((HTTPException,ValueError)):
        resolve(f,f.state)


@pytest.mark.parametrize("field",["workspace_id","source_dataset_id","source_state_id"])
def test_bad_identifiers_rejected(field):
    class Dummy:
        model=1
        version=1
    values=dict(workspace_id=1,source_dataset_id=2,source_state_id=3)
    values[field]=True
    with pytest.raises(ValueError):
        resolve_single_draft(values["workspace_id"],{"owner_key":"test"},
                             WarehouseSqlDraft("SELECT id FROM model_1 LIMIT 1",1,1,("id",),1),
                             source_dataset_id=values["source_dataset_id"],
                             source_state_id=values["source_state_id"])


def test_no_cloud_or_provider_job_in_resolver():
    import ast
    from pathlib import Path
    path=Path(__file__).parents[1]/"app/services/warehouse_sql_binding_service.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(name and name.startswith(("google","boto3","app.warehouse.providers.bigquery"))
                   for name in imports)
