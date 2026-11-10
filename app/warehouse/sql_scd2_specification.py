"""Non-executable SCD2 temporal join specification for a reviewed relationship.

An SCD2 warehouse version is valid in a half-open interval:
    valid_from <= fact event time < valid_to
where a NULL valid_to denotes an unbounded current version.

This emits structured, logical predicate requirements only. Physical serving
column mapping, source-time normalization, provider verification, uniqueness
evidence and executable SQL compilation remain separate mandatory gates.
"""
from dataclasses import dataclass
from typing import Literal

from app.warehouse.relationship_contract import WarehouseRelationship, validate_relationship
from app.schemas.warehouse_modeling import (
    FactConfiguration, DimensionConfiguration, SourceSchema,
)


@dataclass(frozen=True)
class ColumnRef:
    relation: Literal["FACT", "DIMENSION"]
    column: str


@dataclass(frozen=True)
class Equality:
    fact: ColumnRef
    dimension: ColumnRef


@dataclass(frozen=True)
class Scd2TemporalPredicate:
    event_time: ColumnRef
    valid_from: ColumnRef
    valid_to: ColumnRef
    lower_inclusive: bool
    upper_exclusive: bool
    null_upper_is_unbounded: bool


@dataclass(frozen=True)
class Scd2JoinSpecification:
    workspace_id: int
    fact_model_id: int
    fact_model_version_id: int
    dimension_model_id: int
    dimension_model_version_id: int
    equality_keys: tuple[Equality, ...]
    temporal: Scd2TemporalPredicate
    cardinality: Literal["MANY_TO_ONE"] = "MANY_TO_ONE"
    executable: bool = False


def scd2_join_specification(
    relation: WarehouseRelationship,
    fact: FactConfiguration,
    fact_schema: SourceSchema,
    dimension: DimensionConfiguration,
    dimension_schema: SourceSchema,
) -> Scd2JoinSpecification:
    """Require approved semantic compatibility; never infer a relationship.

    Caller must separately authenticate the user and establish that the
    supplied configuration is the reviewed, current, version-pinned record.
    """
    if dimension.scd_type != "SCD_TYPE_2":
        raise ValueError("Historical SCD2 planner requires an SCD Type 2 dimension")
    if relation.temporal_policy != "EVENT_TIME_AS_OF":
        raise ValueError("Historical joins require an explicit EVENT_TIME_AS_OF relationship")
    validate_relationship(relation, fact, fact_schema, dimension, dimension_schema)
    if fact.event_time is None:
        raise ValueError("SCD2 fact event time is missing")

    # Source business keys are logical; physical provider projection names
    # are intentionally not generated or claimed here.
    keys = tuple(
        Equality(
            ColumnRef("FACT", pair.fact_column),
            ColumnRef("DIMENSION", pair.dimension_column),
        )
        for pair in relation.key_pairs
    )
    return Scd2JoinSpecification(
        workspace_id=relation.workspace_id,
        fact_model_id=relation.fact_model_id,
        fact_model_version_id=relation.fact_model_version_id,
        dimension_model_id=relation.dimension_model_id,
        dimension_model_version_id=relation.dimension_model_version_id,
        equality_keys=keys,
        temporal=Scd2TemporalPredicate(
            event_time=ColumnRef("FACT", fact.event_time.column),
            valid_from=ColumnRef("DIMENSION", "valid_from"),
            valid_to=ColumnRef("DIMENSION", "valid_to"),
            lower_inclusive=True,
            upper_exclusive=True,
            null_upper_is_unbounded=True,
        ),
    )
