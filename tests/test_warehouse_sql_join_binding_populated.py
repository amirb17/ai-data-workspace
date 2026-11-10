"""Step 409D: two real independently published warehouse models + reviewed SCD2 join.

All PostgreSQL schemas are disposable; both BigQuery providers use the
in-memory fake client. No live warehouse query or credential access occurs.
"""
from types import SimpleNamespace
from datetime import timedelta

import pytest
from fastapi import HTTPException

from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg, configured, fact
from tests.test_warehouse_provider_postgres import provider_pg
from tests.test_warehouse_serving_postgres import publication, row
from tests.test_warehouse_reliability import NOW, Objects
from tests.test_warehouse_relationship_postgres import MIGRATION
from app.schemas.warehouse import ResourceRegistration
from app.services import warehouse_model_service as models
from app.services import warehouse_resource_service as resources
from app.services import warehouse_relationship_service as relationships
from app.services.warehouse_provider_service import ProviderService
from app.services.warehouse_reliability_service import ReliabilityService
from app.services.warehouse_sql_join_binding_service import resolve_join_draft
from app.storage.incremental_artifacts import IncrementalArtifacts
from app.warehouse.relationship_contract import WarehouseRelationship
from app.warehouse.sql_scd2_draft_planner import Scd2SqlDraft


@pytest.fixture
def two_published_models(provider_pg):
    dim=provider_pg
    pg=dim.pg
    fact_cfg=fact(
        grain={"description":"One event line","identity_columns":["line"]},
        attributes=["id"],
        event_time={"column":"effective","interpretation":"UTC"},
    )
    mid,vid,_,_=configured(pg,fact_cfg,"FACT for joined serving")
    models.approve_version(pg["workspace"],mid,vid,pg["owner"])
    r=resources.register_resource(
        pg["workspace"],mid,vid,pg["owner"],
        ResourceRegistration(
            environment="development",location="asia-south1",generation=1,
            physical_reference={
                "project":"synthetic-project",
                "dataset":f'dr_dev_w{pg["workspace"]}',
                "table":f"m{mid}_v{vid}_g1",
            },
        ),
    )
    with pg["connect"]() as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
    relation=WarehouseRelationship(
        workspace_id=pg["workspace"],
        fact_model_id=mid,fact_model_version_id=vid,
        dimension_model_id=dim.model,dimension_model_version_id=dim.version,
        name="Customer at event time",
        key_pairs=[{"fact_column":"id","dimension_column":"id"}],
        temporal_policy="EVENT_TIME_AS_OF",
        fact_event_time_column="effective",
    )
    registered=relationships.register(pg["workspace"],pg["owner"],relation)
    provider=ProviderService(pg["workspace"],mid,vid,pg["owner"],
                             target_factory=dim.factory)
    f=SimpleNamespace(
        pg=pg,model=mid,version=vid,resource=r["resource_id"],
        mode="APPEND",store=IncrementalArtifacts(Objects()),
        frame=None,state=None,
        service=ReliabilityService(pg["workspace"],mid,vid,pg["owner"]),
        provider=provider,cloud=dim.cloud,factory=dim.factory,targets=dim.targets,
    )
    provider.provision(f.resource)
    return f,dim,registered["relationship_id"]


def draft(f,dim):
    return Scd2SqlDraft(
        normalized_sql="SELECT f.id, d.segment FROM approved logical models",
        workspace_id=f.pg["workspace"],
        fact_model_id=f.model,fact_model_version_id=f.version,
        dimension_model_id=dim.model,dimension_model_version_id=dim.version,
        projected_columns=(("f","id"),("d","segment")),
        row_limit=10,
    )


def bind(f,dim,relationship_id,fact_state,dimension_state):
    return resolve_join_draft(
        f.pg["workspace"],f.pg["owner"],draft(f,dim),
        relationship_id=relationship_id,
        fact_dataset_id=f.pg["dataset"],fact_state_id=fact_state,
        dimension_dataset_id=dim.pg["dataset"],dimension_state_id=dimension_state,
    )


def materialize(f,records=None,*,key="first",at=NOW):
    published=publication(f,records,key=key,at=at)
    receipt=f.provider.materialize_current_publication(f.resource)
    return published,receipt,f.state


def test_two_independent_current_receipts_bind_as_one_nonexecutable_join(two_published_models):
    f,dim,rid=two_published_models
    p1,receipt1,fs=materialize(f,[row("A")])
    # Publishing this separate DIMENSION model changes the dataset's source
    # state but does not change the FACT's immutable publication head.
    p2,receipt2,ds=materialize(dim,[row("A")])
    result=bind(f,dim,rid,fs,ds)
    assert result.executable is False
    assert len(result.models)==2
    assert [m.model_id for m in result.models]==[f.model,dim.model]
    assert [m.publication_id for m in result.models]==[
        p1["publication_id"],p2["publication_id"]]
    assert [m.serving_materialization_id for m in result.models]==[
        receipt1["serving_materialization_id"],receipt2["serving_materialization_id"]]
    assert result.models[0].physical_table != result.models[1].physical_table
    assert [(c.alias,c.logical_name) for c in result.columns]==[
        ("f","id"),("d","segment")]


@pytest.mark.parametrize("advance_side",["fact","dimension"])
def test_advancing_either_head_invalidates_old_source_pin(two_published_models,advance_side):
    f,dim,rid=two_published_models
    _,_,fact_state=materialize(f,[row("A")])
    _,_,dimension_state=materialize(dim,[row("A")])
    assert bind(f,dim,rid,fact_state,dimension_state).executable is False

    side=f if advance_side=="fact" else dim
    later=NOW+timedelta(days=180)
    # A successful new publication advances the selected resource's head
    # before its new serving materialization can be independently recorded.
    publication(side,[row("B",at=later)],key="second",at=later)
    with pytest.raises((HTTPException,ValueError)):
        bind(f,dim,rid,fact_state,dimension_state)
    side.provider.materialize_current_publication(side.resource)
    refreshed_fact=f.state if advance_side=="fact" else fact_state
    refreshed_dim=dim.state if advance_side=="dimension" else dimension_state
    result=bind(f,dim,rid,refreshed_fact,refreshed_dim)
    assert result.executable is False
    assert len(result.models)==2


def test_no_second_publication_may_be_silently_inferred(two_published_models):
    f,dim,rid=two_published_models
    _,_,fact_state=materialize(f,[row("A")])
    with pytest.raises((HTTPException,ValueError)):
        bind(f,dim,rid,fact_state,fact_state)


def test_workspace_owner_is_checked_before_join_publication_resolution(two_published_models):
    f,dim,rid=two_published_models
    _,_,fact_state=materialize(f,[row("A")])
    _,_,dimension_state=materialize(dim,[row("A")])
    with pytest.raises(HTTPException) as exc:
        resolve_join_draft(
            f.pg["workspace"],f.pg["other"],draft(f,dim),
            relationship_id=rid,
            fact_dataset_id=f.pg["dataset"],fact_state_id=fact_state,
            dimension_dataset_id=dim.pg["dataset"],dimension_state_id=dimension_state,
        )
    assert exc.value.status_code==403
