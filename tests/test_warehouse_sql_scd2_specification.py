"""Explicit SCD2 time-window semantics; never execute provider SQL."""
import pytest
from pydantic import ValidationError

from app.schemas.warehouse_modeling import (
    FactConfiguration, DimensionConfiguration, SourceSchema,
)
from app.warehouse.relationship_contract import WarehouseRelationship
from app.warehouse.sql_scd2_specification import scd2_join_specification


def contracts():
    fact=FactConfiguration(
        model_type="FACT",
        grain={"description":"One purchase line","identity_columns":["line_id"]},
        attributes=["product_id","store_id"],
        measures=["amount"],
        event_time={"column":"purchased_at","interpretation":"UTC"},
    )
    dimension=DimensionConfiguration(
        model_type="DIMENSION",
        business_keys=["product_id","store_id"],
        scd_type="SCD_TYPE_2",
        tracked_attributes=["category"],
        effective_time={"column":"changed_at","interpretation":"UTC"},
    )
    fs=SourceSchema(columns=[
        {"name":"line_id","data_type":"STRING"},
        {"name":"product_id","data_type":"STRING"},
        {"name":"store_id","data_type":"STRING"},
        {"name":"amount","data_type":"DECIMAL"},
        {"name":"purchased_at","data_type":"TIMESTAMP"},
    ])
    ds=SourceSchema(columns=[
        {"name":"product_id","data_type":"STRING"},
        {"name":"store_id","data_type":"STRING"},
        {"name":"category","data_type":"STRING"},
        {"name":"changed_at","data_type":"TIMESTAMP"},
    ])
    relation=WarehouseRelationship(
        workspace_id=7,fact_model_id=12,fact_model_version_id=44,
        dimension_model_id=20,dimension_model_version_id=55,
        name="Product at time of purchase",
        key_pairs=[
            {"fact_column":"product_id","dimension_column":"product_id"},
            {"fact_column":"store_id","dimension_column":"store_id"},
        ],
        temporal_policy="EVENT_TIME_AS_OF",fact_event_time_column="purchased_at",
    )
    return relation,fact,fs,dimension,ds


def test_exact_version_pinned_composite_keys_and_half_open_validity():
    result=scd2_join_specification(*contracts())
    assert result.workspace_id==7
    assert (result.fact_model_id,result.fact_model_version_id)==(12,44)
    assert (result.dimension_model_id,result.dimension_model_version_id)==(20,55)
    assert result.executable is False
    assert result.cardinality=="MANY_TO_ONE"
    assert [(p.fact.column,p.dimension.column) for p in result.equality_keys]==[
        ("product_id","product_id"),("store_id","store_id")]
    p=result.temporal
    assert (p.event_time.relation,p.event_time.column)==("FACT","purchased_at")
    assert (p.valid_from.relation,p.valid_from.column)==("DIMENSION","valid_from")
    assert (p.valid_to.relation,p.valid_to.column)==("DIMENSION","valid_to")
    assert p.lower_inclusive and p.upper_exclusive and p.null_upper_is_unbounded


def test_historical_boundaries_are_half_open():
    # Mathematical interpretation only; does not assert provider execution.
    from datetime import datetime,timezone
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    end=datetime(2026,2,1,tzinfo=timezone.utc)
    within=lambda event, upper: start<=event and (upper is None or event<upper)
    assert within(start,end)
    assert not within(end,end)
    assert within(end,None)


@pytest.mark.parametrize(("change","expected"),[
    ({"temporal_policy":"CURRENT_MEMBER","fact_event_time_column":None},"EVENT_TIME_AS_OF"),
    ({"fact_event_time_column":"changed_at"},"fact event-time column"),
    ({"key_pairs":[{"fact_column":"product_id","dimension_column":"product_id"}]},"business key"),
    ({"key_pairs":[{"fact_column":"amount","dimension_column":"product_id"},
                   {"fact_column":"store_id","dimension_column":"store_id"}]},"Join key"),
])
def test_reject_unreviewed_temporal_and_key_semantics(change,expected):
    relation,fact,fs,dimension,ds=contracts()
    relation=WarehouseRelationship.model_validate({**relation.model_dump(),**change})
    with pytest.raises(ValueError,match=expected):
        scd2_join_specification(relation,fact,fs,dimension,ds)


def test_scd1_dimension_cannot_claim_historical_semantics():
    relation,fact,fs,dimension,ds=contracts()
    scd1=dimension.model_copy(update={"scd_type":"SCD_TYPE_1","effective_time":None})
    with pytest.raises(ValueError,match="SCD Type 2"):
        scd2_join_specification(relation,fact,fs,scd1,ds)


def test_mismatched_time_interpretation_fails_closed():
    relation,fact,fs,dimension,ds=contracts()
    changed=dimension.model_copy(update={
        "effective_time":dimension.effective_time.model_copy(
            update={"interpretation":"SOURCE_TIMEZONE","timezone":"Asia/Kolkata"})
    })
    with pytest.raises(ValueError,match="interpretation"):
        scd2_join_specification(relation,fact,fs,changed,ds)


def test_missing_fact_event_time_fails_closed():
    relation,fact,fs,dimension,ds=contracts()
    without=fact.model_copy(update={"event_time":None})
    with pytest.raises(ValueError,match="event"):
        scd2_join_specification(relation,without,fs,dimension,ds)


def test_specification_has_no_cloud_or_database_access():
    import ast
    from pathlib import Path
    path=Path(__file__).parents[1]/"app/warehouse/sql_scd2_specification.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    dependencies=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert not any(n and n.startswith(("app.db","boto3","google","psycopg"))
                   for n in dependencies)
