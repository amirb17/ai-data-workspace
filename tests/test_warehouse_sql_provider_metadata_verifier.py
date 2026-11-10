"""Step 410A: provider metadata is independent of PostgreSQL receipts."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from app.warehouse.providers.base import ErrorCode, ProviderError
from app.warehouse.sql_physical_binding import (
    InternalColumnBinding,InternalModelBinding,InternalDraftBinding,
)
from app.warehouse.sql_provider_metadata_verifier import verify_serving_table_metadata


PROJECT="synthetic-project"
SCHEMA=(("fact_sk","STRING","REQUIRED"),("c0","STRING","NULLABLE"))


def binding():
    return InternalDraftBinding(
        models=(InternalModelBinding(
            model_id=12,model_version_id=44,resource_id=66,generation=1,
            publication_id=77,publication_revision=2,serving_materialization_id=88,
            schema_sha256="a"*64,serving_sha256="b"*64,
            physical_table=f"{PROJECT}.dr_dev_w7.m12_v44_g1_p77_r2",
        ),),
        columns=(InternalColumnBinding("","id","c0","STRING"),),
    )


def metadata(**changes):
    values=dict(
        full_table_id=f"{PROJECT}:dr_dev_w7.m12_v44_g1_p77_r2",
        table_type="TABLE",location="asia-south1",
        schema=[SimpleNamespace(name=n,field_type=t,mode=m) for n,t,m in SCHEMA],
        time_partitioning=None,range_partitioning=None,clustering_fields=None,
        require_partition_filter=False,expires=None,
        labels={"datarise":"warehouse","environment":"development",
                "workspace":"7","model":"12","generation":"1"},
    )
    values.update(changes)
    return SimpleNamespace(**values)


class Fake:
    project=PROJECT
    def __init__(self,table=None,error=None):
        self.table=table if table is not None else metadata()
        self.error=error
        self.calls=[]
    def get_table(self,name,**kwargs):
        self.calls.append((name,kwargs))
        if self.error:
            raise self.error
        return self.table
    def query(self,*args,**kwargs):
        pytest.fail("Query execution is forbidden")
    def load_table_from_json(self,*args,**kwargs):
        pytest.fail("Provider mutation is forbidden")


def verify(*,bound=None,client=None,schemas=None,project=PROJECT):
    return verify_serving_table_metadata(
        binding() if bound is None else bound,workspace_id=7,
        expected_schemas={12:SCHEMA} if schemas is None else schemas,
        client=Fake() if client is None else client,project=project,
    )


def test_live_metadata_required_and_no_query_is_executed():
    client=Fake()
    found=verify(client=client)
    assert len(found)==1
    assert found[0].table_metadata_verified
    assert found[0].row_contents_verified is False
    assert found[0].query_execution_authorized is False
    assert client.calls==[(binding().models[0].physical_table,
                            {"retry":None,"timeout":10})]


@pytest.mark.parametrize("changed",[
    {"full_table_id":"other:dr_dev_w7.m12_v44_g1_p77_r2"},
    {"table_type":"VIEW"},
    {"location":"europe-west1"},
    {"schema":[SimpleNamespace(name="c0",field_type="STRING",mode="NULLABLE")]},
    {"time_partitioning":object()},
    {"range_partitioning":object()},
    {"clustering_fields":["c0"]},
    {"require_partition_filter":True},
    {"expires":"tomorrow"},
    {"labels":{"datarise":"warehouse","environment":"development",
               "workspace":"8","model":"12","generation":"1"}},
])
def test_physical_provider_metadata_drift_is_rejected(changed):
    with pytest.raises(ProviderError) as caught:
        verify(client=Fake(metadata(**changed)))
    assert caught.value.code==ErrorCode.SCHEMA_MISMATCH


def test_missing_table_or_provider_error_fails_closed():
    with pytest.raises(ProviderError) as caught:
        verify(client=Fake(error=RuntimeError("provider secret")))
    assert caught.value.uncertain
    assert "provider secret" not in str(caught.value)


def test_receipt_cannot_substitute_for_actual_provider_read():
    client=Fake()
    bad=replace(binding().models[0],physical_table="other-project.dataset.anything")
    with pytest.raises(ProviderError):
        verify(bound=replace(binding(),models=(bad,)),client=client)
    assert client.calls==[]


def test_project_and_expected_schema_are_required():
    with pytest.raises(ProviderError):
        verify(client=SimpleNamespace(project="other-project",get_table=lambda *a:metadata()))
    with pytest.raises(ValueError):
        verify(schemas={})


def test_no_execution_imports():
    import ast
    from pathlib import Path
    tree=ast.parse((Path(__file__).parents[1]/"app/warehouse/sql_provider_metadata_verifier.py").read_text())
    modules=[i.module for i in ast.walk(tree) if isinstance(i,ast.ImportFrom)]
    assert not any(n and n.startswith(("app.db","app.query_governance.providers.bigquery",
                                      "app.warehouse.providers.bigquery")) for n in modules)
