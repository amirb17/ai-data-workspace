"""Step 410B: actual stateful fake BigQuery table metadata.

The PostgreSQL fixture publishes independent FACT/DIMENSION heads and builds
immutable serving tables using the real BigQuery adapter with fake SDK IO.
We obtain expected complete schemas from approved model contexts, not from
handwritten column subsets. No analyst SQL or live BigQuery traffic.
"""
from copy import deepcopy

import pytest

from app.db import warehouse_repository as repository
from app.processing.metric_warehouse_compiler import MetricWarehouseSource
from app.services.metric_warehouse_binding_service import _context
from app.warehouse.providers import model_adapter
from app.warehouse.providers.base import ProviderError, ErrorCode
from app.warehouse.sql_provider_metadata_verifier import verify_serving_table_metadata
from tests.test_warehouse_sql_join_binding_populated import (
    two_published_models, materialize, bind, row,
)


def verified_inputs(f,dim,rid):
    _,_,fs=materialize(f,[row("A")])
    _,_,ds=materialize(dim,[row("A",value=2)])
    bound=bind(f,dim,rid,fs,ds)
    expected={}
    with f.pg["connect"]() as conn:
        rows=repository.metric_warehouse_bindings(
            conn,f.pg["workspace"],[f.pg["dataset"],dim.pg["dataset"]],
            "development",
        )
        for model in bound.models:
            matches=[r for r in rows
                     if r["warehouse_model_id"]==model.model_id
                     and r["warehouse_model_version_id"]==model.model_version_id
                     and r["publication_id"]==model.publication_id]
            assert len(matches)==1
            expected[model.model_id]=model_adapter.schema(_context(matches[0]))
    return bound,expected


def inspect(f, bound, schemas, *, client=None):
    return verify_serving_table_metadata(
        bound,workspace_id=f.pg["workspace"],
        expected_schemas=schemas,
        client=f.cloud if client is None else client,
        project="synthetic-project",location="asia-south1",
    )


def test_both_real_provider_style_publication_tables_match_complete_schemas(two_published_models):
    f,dim,rid=two_published_models
    bound,schemas=verified_inputs(f,dim,rid)
    assert len(schemas)==2
    assert any(column[0]=="valid_from" for column in schemas[dim.model])
    assert any(column[0]=="is_active" for column in schemas[f.model])
    assert len(schemas[f.model])>len(bound.columns)
    result=inspect(f,bound,schemas)
    assert len(result)==2
    assert all(r.table_metadata_verified and not r.row_contents_verified
               and not r.query_execution_authorized for r in result)
    assert all(model.physical_table in f.cloud.tables for model in bound.models)


@pytest.mark.parametrize("side",["fact","dimension"])
def test_missing_either_physical_table_fails_closed(two_published_models,side):
    f,dim,rid=two_published_models
    bound,schemas=verified_inputs(f,dim,rid)
    selected=bound.models[0 if side=="fact" else 1]
    f.cloud.delete_table(selected.physical_table)
    with pytest.raises(ProviderError) as error:
        inspect(f,bound,schemas)
    assert error.value.code==ErrorCode.RESOURCE_NOT_FOUND


@pytest.mark.parametrize("side",["fact","dimension"])
def test_full_schema_drift_fails_closed(two_published_models,side):
    f,dim,rid=two_published_models
    bound,schemas=verified_inputs(f,dim,rid)
    selected=bound.models[0 if side=="fact" else 1]
    saved=f.cloud.tables[selected.physical_table]
    saved.schema=saved.schema[:-1]
    with pytest.raises(ProviderError) as error:
        inspect(f,bound,schemas)
    assert error.value.code==ErrorCode.SCHEMA_MISMATCH


def test_wrong_publication_labels_fail_closed(two_published_models):
    f,dim,rid=two_published_models
    bound,schemas=verified_inputs(f,dim,rid)
    selected=bound.models[1]
    table=f.cloud.tables[selected.physical_table]
    table.labels={**table.labels,"workspace":"wrong"}
    with pytest.raises(ProviderError) as error:
        inspect(f,bound,schemas)
    assert error.value.code==ErrorCode.SCHEMA_MISMATCH


def test_get_table_only_no_query_execution(two_published_models,monkeypatch):
    f,dim,rid=two_published_models
    bound,schemas=verified_inputs(f,dim,rid)
    before=list(f.cloud.calls)
    def forbidden(*args,**kwargs):
        pytest.fail("Metadata verifier attempted analyst SQL or a write")
    monkeypatch.setattr(f.cloud,"query",forbidden)
    monkeypatch.setattr(f.cloud,"load_table_from_json",forbidden)
    assert len(inspect(f,bound,schemas))==2
    assert f.cloud.calls==before
