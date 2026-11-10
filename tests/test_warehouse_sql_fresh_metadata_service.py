"""Step 410D: post-provider metadata validation cannot accept stale pins."""
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.services.warehouse_sql_fresh_metadata_service import (
    verify_current_publication_metadata,
)
from app.warehouse.sql_physical_binding import InternalDraftBinding
from tests.test_warehouse_sql_provider_metadata_verifier import (
    Fake, binding, SCHEMA,
)
from tests.test_warehouse_sql_provider_metadata_populated import (
    verified_inputs,
)
from tests.test_warehouse_sql_join_binding_populated import (
    two_published_models, bind, materialize, row,
)
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_provider_postgres import provider_pg
from tests.test_warehouse_serving_postgres import publication
from tests.test_warehouse_reliability import NOW


def verify(resolver,client=None):
    return verify_current_publication_metadata(
        resolve_current=resolver,
        workspace_id=7,expected_schemas={12:SCHEMA},
        client=Fake() if client is None else client,
        project="synthetic-project",
    )


def test_matching_pre_and_post_publication_descriptor_is_diagnostic_only():
    reads=[]
    def fresh():
        reads.append(1)
        return binding()
    result=verify(fresh)
    assert len(reads)==2
    assert result.publication_pins_current_after_read
    assert not result.row_contents_verified
    assert not result.query_execution_authorized


def test_stale_post_read_publication_rejected_even_if_provider_table_exists():
    before=binding()
    after=replace(before,models=(
        replace(before.models[0],publication_id=78),))
    snapshots=iter((before,after))
    with pytest.raises(ValueError,match="WAREHOUSE_PUBLICATION_CHANGED"):
        verify(lambda:next(snapshots))


def test_only_full_unchanged_binding_is_accepted():
    before=binding()
    changed=replace(before,models=(
        replace(before.models[0],serving_materialization_id=99),))
    snapshots=iter((before,changed))
    with pytest.raises(ValueError,match="WAREHOUSE_PUBLICATION_CHANGED"):
        verify(lambda:next(snapshots))


def test_missing_authorized_resolver_rejected():
    with pytest.raises(ValueError):
        verify(None)


def test_actual_two_model_post_read_refresh_stays_current(two_published_models):
    f,dim,rid=two_published_models
    bound,schemas=verified_inputs(f,dim,rid)
    fs,ds=f.state,dim.state
    calls=[]
    def resolver():
        calls.append(1)
        return bind(f,dim,rid,fs,ds)
    result=verify_current_publication_metadata(
        resolve_current=resolver,workspace_id=f.pg["workspace"],
        expected_schemas=schemas,client=f.cloud,project="synthetic-project",
    )
    assert len(calls)==2
    assert len(result.attestations)==2
    assert result.query_execution_authorized is False


def test_actual_post_read_publication_head_change_is_rejected(two_published_models):
    f,dim,rid=two_published_models
    bound,schemas=verified_inputs(f,dim,rid)
    fs,ds=f.state,dim.state
    # Inject a publication between the first successful provider metadata
    # read and the PostgreSQL re-resolution. Still no analyst SQL is run.
    class AdvancingClient:
        project="synthetic-project"
        advanced=False
        def get_table(self,name,**kwargs):
            found=f.cloud.get_table(name,**kwargs)
            if not self.advanced:
                self.advanced=True
                later=NOW+timedelta(days=180)
                publication(f,[row("B",value=2,at=later)],
                            key="second",at=later)
            return found
    with pytest.raises(HTTPException) as caught:
        verify_current_publication_metadata(
            resolve_current=lambda:bind(f,dim,rid,fs,ds),
            workspace_id=f.pg["workspace"],expected_schemas=schemas,
            client=AdvancingClient(),project="synthetic-project",
        )
    assert caught.value.status_code == 409
