"""Review-only deterministic relationships; ordered columns permit future composite pairs."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Endpoint(StrictModel):
    dataset_id: int
    dataset_name: str
    columns: list[str] = Field(min_length=1, max_length=8)
    normalized_columns: list[str]
    @model_validator(mode='after')
    def ordered_columns(self):
        if len(self.columns)!=len(set(self.columns)) or len(self.columns)!=len(self.normalized_columns):
            raise ValueError('Invalid relationship columns')
        return self

class SourcePin(StrictModel):
    dataset_id: int
    state_id: int
    state_version: int
    dataset_version_id: int
    policy_id: int
    profile_id: int
    profile_version: int
    schema_version: int
    dataset_suggestion_id: int | None = None

class ColumnStatistics(StrictModel):
    rows: int = Field(ge=0)
    non_null_rows: int = Field(ge=0)
    distinct_keys: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    null_ratio: float | None = Field(default=None, ge=0, le=1)
    uniqueness_ratio: float | None = Field(default=None, ge=0, le=1)
    @model_validator(mode='after')
    def consistent_counts(self):
        if not 0<=self.distinct_keys<=self.non_null_rows<=self.rows or self.duplicate_rows!=self.non_null_rows-self.distinct_keys:
            raise ValueError('Invalid relationship counts')
        expected_null=(self.rows-self.non_null_rows)/self.rows if self.rows else None
        if self.null_ratio!=expected_null:
            raise ValueError('Invalid null ratio')
        expected_unique=self.distinct_keys/self.non_null_rows if self.non_null_rows else None
        if self.uniqueness_ratio!=expected_unique:
            raise ValueError('Invalid uniqueness ratio')
        return self

class Overlap(StrictModel):
    method: Literal['EXACT_BOUNDED']
    child_non_null_distinct_keys: int = Field(ge=0)
    matched_distinct_keys: int = Field(ge=0)
    missing_distinct_keys: int = Field(ge=0)
    child_to_parent_coverage: float | None = Field(default=None, ge=0, le=1)
    parent_referenced_ratio: float | None = Field(default=None, ge=0, le=1)
    @model_validator(mode='after')
    def consistent_coverage(self):
        if self.matched_distinct_keys+self.missing_distinct_keys!=self.child_non_null_distinct_keys:
            raise ValueError('Invalid overlap counts')
        expected=self.matched_distinct_keys/self.child_non_null_distinct_keys if self.child_non_null_distinct_keys else None
        if self.child_to_parent_coverage!=expected: raise ValueError('Invalid coverage ratio')
        return self

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
    source_pins: list[SourcePin]
    @model_validator(mode='after')
    def consistent_context(self):
        ids={self.parent.dataset_id,self.child.dataset_id}
        if len(ids)!=2 or len(self.source_pins)!=2 or {p.dataset_id for p in self.source_pins}!=ids:
            raise ValueError('Invalid relationship source references')
        if sum(x.points for x in self.score_components)!=self.deterministic_score:
            raise ValueError('Invalid evidence score')
        s=self.signals; o=s.overlap
        if o.child_non_null_distinct_keys!=s.child.distinct_keys or o.matched_distinct_keys>s.parent.distinct_keys:
            raise ValueError('Invalid overlap context')
        expected=o.matched_distinct_keys/s.parent.distinct_keys if s.parent.distinct_keys else None
        if o.parent_referenced_ratio!=expected: raise ValueError('Invalid parent referenced ratio')
        safe=bool(s.datatype_compatible and s.parent.non_null_rows and s.parent.duplicate_rows==0 and s.parent.null_ratio==0 and o.child_to_parent_coverage==1)
        if self.can_confirm!=safe: raise ValueError('Invalid confirmation eligibility')
        return self

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
    source_pins: list[SourcePin]
    review_revision: int
    candidates: list[Candidate]
    reviewed_relationships: list[ReviewedRelationship]

class ReviewRequest(StrictModel):
    expected_review_version: int = Field(ge=0)
