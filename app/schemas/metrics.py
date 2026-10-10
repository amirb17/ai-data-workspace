"""Bounded non-recursive expression graph. Provider output never includes trust/status."""
from typing import Literal
from pydantic import Field
from app.schemas.semantic_suggestion import Strict

class ColumnRef(Strict):
    dataset_id: int = Field(gt=0)
    column: str = Field(min_length=1,max_length=256)

class MetricFilter(Strict):
    column: ColumnRef
    operator: Literal['EQ']
    category_ref: str = Field(min_length=1,max_length=80)

class Node(Strict):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,31}$')
    op: Literal['COLUMN','MULTIPLY','COUNT','COUNT_DISTINCT','SUM','AVG','MIN','MAX','DIVIDE']
    column: ColumnRef | None = None
    args: list[str] = Field(default_factory=list,max_length=2)
    filters: list[MetricFilter] = Field(default_factory=list,max_length=3)

class Formula(Strict):
    nodes: list[Node] = Field(min_length=1,max_length=24)
    root: str = Field(max_length=32)
    zero_denominator: Literal['NULL'] = 'NULL'
    scale: Literal[1,100] = 1

class MetricProposal(Strict):
    name: str = Field(min_length=1,max_length=120)
    description: str = Field(max_length=500)
    metric_type: Literal['COUNT','COUNT_DISTINCT','SUM','AVG','MIN','MAX','RATIO','RATE','PERCENTAGE']
    base_dataset_id: int = Field(gt=0)
    base_entity: str = Field(min_length=1,max_length=120)
    grain: Literal['RECORD','ENTITY']
    grain_keys: list[str] = Field(default_factory=list,max_length=8)
    formula: Formula
    dimensions: list[ColumnRef] = Field(default_factory=list,max_length=3)
    time_field: ColumnRef | None = None
    time_bucket: Literal['DAY','WEEK','MONTH'] | None = None
    relationship_ids: list[int] = Field(default_factory=list,max_length=1)
    default_filters: list[MetricFilter] = Field(default_factory=list,max_length=3)
    unit: Literal['NUMBER','RATIO','PERCENTAGE']
    confidence: float = Field(ge=0,le=1)
    rationale: str = Field(max_length=500)
    unresolved_assumptions: list[str] = Field(default_factory=list,max_length=5)

class MetricOutput(Strict):
    metrics: list[MetricProposal] = Field(max_length=20)

class MetricReview(Strict):
    expected_review_version: int = Field(ge=0)
