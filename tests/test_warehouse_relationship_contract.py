"""Pure relationship semantics; no DB, provider, or cloud calls."""
import pytest
from pydantic import ValidationError
from app.schemas.warehouse_modeling import FactConfiguration, DimensionConfiguration, SourceSchema
from app.warehouse.relationship_contract import WarehouseRelationship, validate_relationship


def schemas():
    fact = FactConfiguration(
        model_type="FACT",
        grain={"description": "One sales line", "identity_columns": ["line_id"]},
        attributes=["product_id", "store_id"],
        measures=["amount"],
        event_time={"column": "purchased_at", "interpretation": "UTC"},
    )
    dimension = DimensionConfiguration(
        model_type="DIMENSION",
        business_keys=["product_id", "store_id"],
        scd_type="SCD_TYPE_2",
        tracked_attributes=["category"],
        effective_time={"column": "changed_at", "interpretation": "UTC"},
    )
    fs = SourceSchema(columns=[
        {"name": "line_id", "data_type": "STRING"},
        {"name": "product_id", "data_type": "STRING"},
        {"name": "store_id", "data_type": "STRING"},
        {"name": "amount", "data_type": "DECIMAL"},
        {"name": "purchased_at", "data_type": "TIMESTAMP"},
    ])
    ds = SourceSchema(columns=[
        {"name": "product_id", "data_type": "STRING"},
        {"name": "store_id", "data_type": "STRING"},
        {"name": "category", "data_type": "STRING"},
        {"name": "changed_at", "data_type": "TIMESTAMP"},
    ])
    return fact, fs, dimension, ds


def payload(**overrides):
    p = {
        "workspace_id": 1,
        "fact_model_id": 10,
        "fact_model_version_id": 11,
        "dimension_model_id": 20,
        "dimension_model_version_id": 21,
        "name": "Product at purchase",
        "key_pairs": [
            {"fact_column": "product_id", "dimension_column": "product_id"},
            {"fact_column": "store_id", "dimension_column": "store_id"},
        ],
        "temporal_policy": "EVENT_TIME_AS_OF",
        "fact_event_time_column": "purchased_at",
    }
    p.update(overrides)
    return p


def test_composite_scd2_join_requires_explicit_time_and_all_keys():
    relationship = WarehouseRelationship.model_validate(payload())
    assert validate_relationship(relationship, *schemas()) is relationship
    assert relationship.cardinality == "MANY_TO_ONE"


@pytest.mark.parametrize("change", [
    {"key_pairs": [{"fact_column": "product_id", "dimension_column": "product_id"}]},
    {"key_pairs": [{"fact_column": "amount", "dimension_column": "product_id"},
                   {"fact_column": "store_id", "dimension_column": "store_id"}]},
    {"key_pairs": [{"fact_column": "unknown", "dimension_column": "product_id"},
                   {"fact_column": "store_id", "dimension_column": "store_id"}]},
])
def test_invalid_semantic_keys_are_rejected(change):
    with pytest.raises(ValueError):
        validate_relationship(WarehouseRelationship.model_validate(payload(**change)), *schemas())


@pytest.mark.parametrize("change", [
    {"workspace_id": 0},
    {"fact_model_id": 20},
    {"name": " "},
    {"cardinality": "MANY_TO_MANY"},
    {"physical_table": "secret"},
    {"key_pairs": [{"fact_column": "product_id", "dimension_column": "product_id"}] * 2},
    {"temporal_policy": "EVENT_TIME_AS_OF", "fact_event_time_column": None},
    {"temporal_policy": "CURRENT_MEMBER", "fact_event_time_column": "purchased_at"},
])
def test_invalid_structure_fails_closed(change):
    with pytest.raises(ValidationError):
        WarehouseRelationship.model_validate(payload(**change))


def test_scd2_cannot_use_current_only_join():
    fact, fs, dim, ds = schemas()
    with pytest.raises(ValueError, match="SCD2"):
        validate_relationship(
            WarehouseRelationship.model_validate(
                payload(temporal_policy="CURRENT_MEMBER", fact_event_time_column=None)
            ), fact, fs, dim, ds
        )


def test_scd1_current_member_is_supported_but_historical_join_is_not():
    fact, fs, dim, ds = schemas()
    dim = dim.model_copy(update={"scd_type": "SCD_TYPE_1", "effective_time": None})
    relationship = WarehouseRelationship.model_validate(
        payload(temporal_policy="CURRENT_MEMBER", fact_event_time_column=None)
    )
    assert validate_relationship(relationship, fact, fs, dim, ds) is relationship
    with pytest.raises(ValueError, match="SCD1"):
        validate_relationship(WarehouseRelationship.model_validate(payload()), fact, fs, dim, ds)


def test_join_time_type_and_timezone_must_agree():
    fact, fs, dim, ds = schemas()
    dim = dim.model_copy(update={
        "effective_time": dim.effective_time.model_copy(update={
            "interpretation": "SOURCE_TIMEZONE", "timezone": "Asia/Kolkata"
        })
    })
    with pytest.raises(ValueError, match="interpretation"):
        validate_relationship(WarehouseRelationship.model_validate(payload()), fact, fs, dim, ds)


def test_relationship_does_not_require_cloud_credentials():
    import ast
    from pathlib import Path
    p = Path(__file__).parents[1] / "app/warehouse/relationship_contract.py"
    tree = ast.parse(p.read_text())
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(name.startswith(("google", "boto3", "duckdb", "psycopg")) for name in imports)
