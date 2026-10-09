"""Review-only deterministic relationships; ordered columns permit future composite pairs."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Endpoint(StrictModel):
    dataset_id: int
    dataset_name: str
    columns: list[str] = Field(min_length=1, max_length=8)
    normalized_columns: list[str]

class ColumnStatistics(StrictModel):
    rows: int = Field(ge=0)
    non_null_rows: int = Field(ge=0)
    distinct_keys: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    null_ratio: float | None = Field(default=None, ge=0, le=1)
    uniqueness_ratio: float | None = Field(default=None, ge=0, le=1)

class Overlap(StrictModel):
    method: Literal['EXACT_BOUNDED']
    child_non_null_distinct_keys: int = Field(ge=0)
    matched_distinct_keys: int = Field(ge=0)
    missing_distinct_keys: int = Field(ge=0)
    child_to_parent_coverage: float | None = Field(default=None, ge=0, le=1)
    parent_referenced_ratio: float | None = Field(default=None, ge=0, le=1)

class Signals(StrictModel):
    name_features: list[str]
    datatype_compatible: bool
    parent_type: str
    child_type: str
    configured_parent_key: bool
    parent: ColumnStatistics
    child: ColumnStatistics
    overlap: Overlap
    parent_semantic_role: str | None = None
    child_semantic_role: str | None = None

class ScoreComponent(StrictModel):
    name: str
    points: int = Field(ge=0, le=100)
    explanation: str

class RelationshipEvidence(StrictModel):
    workspace_id: int
    candidate_key: str
    parent: Endpoint
    child: Endpoint
    candidate_direction: Literal['PARENT_TO_CHILD']
    candidate_cardinality: Literal['ONE_TO_ONE','ONE_TO_MANY','MANY_TO_ONE','MANY_TO_MANY_CANDIDATE','UNKNOWN']
    signals: Signals
    deterministic_score: int = Field(ge=0, le=100)
    score_components: list[ScoreComponent]
    warnings: list[str]
    can_confirm: bool
    structural_signature: str
    source_pins: list[dict]

class Candidate(StrictModel):
    candidate_id: int
    run_id: int
    candidate_status: Literal['CANDIDATE','REVIEW_REQUIRED']
    evidence: RelationshipEvidence
    review_status: Literal['REVIEW_REQUIRED','CONFIRMED','REJECTED']
    review_version: int

class ReviewedRelationship(StrictModel):
    candidate_id: int
    relationship_version: int
    status: Literal['CONFIRMED','REJECTED']
    reviewed_by: int
    reviewed_at: datetime
    evidence: RelationshipEvidence
    structural_status: Literal['CURRENT','REVALIDATION_REQUIRED','UNAVAILABLE']
    verification_status: Literal['CURRENT','STALE']

class RelationshipsView(StrictModel):
    workspace_id: int
    status: Literal['NOT_GENERATED','STALE','DISCOVERING','READY','FAILED']
    run_id: int | None
    run_version: int | None
    failure_code: str | None
    can_discover: bool
    readiness_message: str
    coverage: dict
    semantic_context: dict
    limits: dict
    candidates: list[Candidate]
    reviewed_relationships: list[ReviewedRelationship]

class ReviewRequest(StrictModel):
    expected_review_version: int = Field(ge=0)
