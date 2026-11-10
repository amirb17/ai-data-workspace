"""Internal binding contract: no physical SQL, no provider access."""
from dataclasses import replace
import pytest

from app.processing.metric_warehouse_compiler import MetricWarehouseSource
from app.warehouse.sql_draft_planner import WarehouseSqlDraft
from app.warehouse.sql_join_draft_planner import WarehouseJoinDraft
from app.warehouse.sql_scd2_draft_planner import Scd2SqlDraft
from app.warehouse.sql_physical_binding import bind_draft


def source(mid,version,kind):
    return MetricWarehouseSource(
        dataset_id=mid,state_id=10,model_id=mid,model_version_id=version,
        resource_id=mid+100,generation=1,publication_id=mid+200,
        publication_revision=2,serving_materialization_id=mid+300,
        serving_sha256="a"*64,serving_row_count=10,
        physical_table=f"synthetic-project.dr_dev_w7.m{mid}_v{version}_g1_p{mid+200}_r2",
        columns=(("product_id","c0","STRING"),("amount","c1","BIGNUMERIC"),
                 ("category","c2","STRING"),("purchased_at","c3","TIMESTAMP")),
        grain_keys=("product_id",) if kind=="FACT" else (),
        business_keys=("product_id",) if kind=="DIMENSION" else (),
        configuration_sha256="b"*64,source_vector_sha256="c"*64,
        model_type=kind,
        event_time_column="purchased_at" if kind=="FACT" else None,
        event_time_interpretation="UTC" if kind=="FACT" else None,
        schema_sha256="d"*64,
    )


def single():
    return WarehouseSqlDraft("SELECT amount FROM model_12 LIMIT 5",12,44,("amount",),5)


def joined(historical=False):
    klass=Scd2SqlDraft if historical else WarehouseJoinDraft
    common=dict(normalized_sql="SELECT f.amount, d.category FROM model_12 f JOIN model_20 d",
                fact_model_id=12,fact_model_version_id=44,
                dimension_model_id=20,dimension_model_version_id=55,
                projected_columns=(("f","amount"),("d","category")),row_limit=5)
    if historical:
        return klass(workspace_id=7,**common)
    return klass(relationship_id=9,**common)


def test_single_table_maps_only_pinned_physical_columns():
    result=bind_draft(single(),fact=source(12,44,"FACT"))
    assert result.executable is False
    assert len(result.models)==1
    assert (result.models[0].publication_revision,result.models[0].serving_materialization_id)==(2,312)
    assert [(c.logical_name,c.physical_name,c.physical_type) for c in result.columns]==[
        ("amount","c1","BIGNUMERIC")]
    assert not hasattr(result,"normalized_sql")


@pytest.mark.parametrize("historical",[False,True])
def test_fact_dimension_bind_to_independent_publication_pins(historical):
    result=bind_draft(joined(historical),fact=source(12,44,"FACT"),
                      dimension=source(20,55,"DIMENSION"))
    assert result.executable is False
    assert len(result.models)==2
    assert [m.model_id for m in result.models]==[12,20]
    assert [(c.alias,c.physical_name) for c in result.columns]==[
        ("f","c1"),("d","c2")]


def test_stale_model_version_or_wrong_type_block():
    with pytest.raises(ValueError):
        bind_draft(single(),fact=source(12,45,"FACT"))
    with pytest.raises(ValueError):
        bind_draft(joined(),fact=source(12,44,"FACT"),
                   dimension=source(20,55,"FACT"))


def test_missing_schema_digest_blocks():
    with pytest.raises(ValueError):
        bind_draft(single(),fact=replace(source(12,44,"FACT"),schema_sha256=None))


def test_missing_or_extra_dimension_blocks():
    with pytest.raises(ValueError):
        bind_draft(joined(),fact=source(12,44,"FACT"))
    with pytest.raises(ValueError):
        bind_draft(single(),fact=source(12,44,"FACT"),dimension=source(20,55,"DIMENSION"))


def test_mismatched_column_contract_blocks():
    draft=replace(single(),projected_columns=("unknown",))
    with pytest.raises(ValueError):
        bind_draft(draft,fact=source(12,44,"FACT"))


def test_untyped_publication_evidence_blocks():
    with pytest.raises(ValueError):
        bind_draft(single(),fact={"model_id":12})


def test_never_convert_draft_to_executable():
    with pytest.raises(ValueError):
        bind_draft(replace(single(),executable=True),fact=source(12,44,"FACT"))


def test_no_provider_sql_or_database_access():
    import ast
    from pathlib import Path
    file=Path(__file__).parents[1]/"app/warehouse/sql_physical_binding.py"
    tree=ast.parse(file.read_text(encoding="utf-8"))
    imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(name and name.startswith(("app.db","google","boto3","psycopg"))
                   for name in imports)
