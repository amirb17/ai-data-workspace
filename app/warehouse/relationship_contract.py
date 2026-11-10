"""Strict, provider-neutral FACT-to-DIMENSION relationship contracts.

No relationship is inferred or published by this module. A later reviewed
metadata workflow must persist these records before the UI displays edges.
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.warehouse_modeling import FactConfiguration, DimensionConfiguration, SourceSchema


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JoinPair(Contract):
    fact_column: str = Field(min_length=1, max_length=255)
    dimension_column: str = Field(min_length=1, max_length=255)


class WarehouseRelationship(Contract):
    schema_version: Literal[1] = 1
    workspace_id: int = Field(gt=0)
    fact_model_id: int = Field(gt=0)
    fact_model_version_id: int = Field(gt=0)
    dimension_model_id: int = Field(gt=0)
    dimension_model_version_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    key_pairs: tuple[JoinPair, ...] = Field(min_length=1, max_length=32)
    cardinality: Literal["MANY_TO_ONE"] = "MANY_TO_ONE"
    temporal_policy: Literal["CURRENT_MEMBER", "EVENT_TIME_AS_OF"]
    fact_event_time_column: str | None = None

    @model_validator(mode="after")
    def structural(self):
        if self.fact_model_id == self.dimension_model_id:
            raise ValueError("Relationship must join different models")
        if not self.name.strip() or self.name != self.name.strip():
            raise ValueError("Explicit normalized display name required")
        fact_names = [pair.fact_column for pair in self.key_pairs]
        dimension_names = [pair.dimension_column for pair in self.key_pairs]
        if len(set(fact_names)) != len(fact_names) or len(set(dimension_names)) != len(dimension_names):
            raise ValueError("Join keys must not repeat")
        if self.temporal_policy == "EVENT_TIME_AS_OF":
            if not self.fact_event_time_column:
                raise ValueError("Historical relationship requires fact event time")
        elif self.fact_event_time_column is not None:
            raise ValueError("Current-member join must not specify historical time")
        return self


def validate_relationship(
    relationship: WarehouseRelationship,
    fact: FactConfiguration,
    fact_schema: SourceSchema,
    dimension: DimensionConfiguration,
    dimension_schema: SourceSchema,
) -> WarehouseRelationship:
    """Check explicit source-column keys and conservative SCD join semantics.

    This checks semantic compatibility, not actual row uniqueness or
    publication readiness. Those require independent evidence.
    """
    fact_columns = {col.name: col for col in fact_schema.columns}
    dimension_columns = {col.name: col for col in dimension_schema.columns}
    keys = tuple(pair.dimension_column for pair in relationship.key_pairs)
    if len(keys) != len(dimension.business_keys) or set(keys) != set(dimension.business_keys):
        raise ValueError("Relationship must map every dimension business key exactly")
    allowed_fact = set(fact.grain.identity_columns) | set(fact.attributes)
    for pair in relationship.key_pairs:
        left = fact_columns.get(pair.fact_column)
        right = dimension_columns.get(pair.dimension_column)
        if left is None or right is None or pair.fact_column not in allowed_fact:
            raise ValueError("Join key must be an approved fact identity/attribute and a dimension business key")
        if left.data_type != right.data_type or left.data_type == "FLOAT":
            raise ValueError("Join key types must match exactly without FLOAT")

    if dimension.scd_type == "SCD_TYPE_2":
        if relationship.temporal_policy != "EVENT_TIME_AS_OF":
            raise ValueError("SCD2 requires a historical as-of join")
        if fact.event_time is None or dimension.effective_time is None:
            raise ValueError("SCD2 join requires both modeled event and effective times")
        if relationship.fact_event_time_column != fact.event_time.column:
            raise ValueError("Historical join must use the reviewed fact event-time column")
        fact_time = fact_columns.get(relationship.fact_event_time_column)
        dim_time = dimension_columns.get(dimension.effective_time.column)
        if fact_time is None or dim_time is None:
            raise ValueError("Historical time columns must exist")
        if (fact_time.data_type != dim_time.data_type
                or fact_time.data_type not in ("DATE", "TIMESTAMP")):
            raise ValueError("Historical join time types must match")
        if fact.event_time.interpretation != dimension.effective_time.interpretation:
            raise ValueError("Historical time interpretation must agree")
        if fact.event_time.timezone != dimension.effective_time.timezone:
            raise ValueError("Historical timezones must agree")
    elif relationship.temporal_policy != "CURRENT_MEMBER":
        raise ValueError("SCD1 uses current-member semantics")
    return relationship
