"""Workspace suggestions and the future evidence handoff; never semantic approvals."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from app.schemas.semantic_suggestion import Strict, Classification, ShortText, SuggestionRecord
from app.schemas.semantic_evidence import SemanticEvidenceBundle

class Contribution(Strict):
    name: str = Field(min_length=1,max_length=120)
    confidence: float = Field(ge=0,le=1)
    contributing_datasets: list[int] = Field(min_length=1,max_length=50)
    rationale: str = Field(max_length=500)

class Entity(Strict):
    canonical_name: str = Field(min_length=1,max_length=120)
    confidence: float = Field(ge=0,le=1)
    contributing_datasets: list[int] = Field(min_length=1,max_length=50)
    rationale: str = Field(max_length=500)

class DatasetRole(Strict):
    dataset_id: int
    role: Literal['ENTITY_MASTER','TRANSACTION','EVENT','REFERENCE','SNAPSHOT','BRIDGE_CANDIDATE','AGGREGATE','UNKNOWN']
    confidence: float = Field(ge=0,le=1)
    rationale: str = Field(max_length=500)

class WorkspaceReasoning(Strict):
    domain: Classification
    subdomain: Classification
    business_processes: list[Contribution] = Field(max_length=30)
    entities: list[Entity] = Field(max_length=50)
    dataset_roles: list[DatasetRole] = Field(max_length=50)
    overall_confidence: float = Field(ge=0,le=1)
    warnings: list[ShortText] = Field(max_length=10)
    unresolved_questions: list[ShortText] = Field(max_length=10)

class Exclusion(BaseModel):
    dataset_id: int
    dataset_name: str
    reason: str

class Coverage(BaseModel):
    datasets_total: int
    datasets_analyzed: int
    datasets_excluded: int
    partial: bool
    excluded: list[Exclusion]
    @model_validator(mode='after')
    def accurate_counts(self):
        if min(self.datasets_total,self.datasets_analyzed,self.datasets_excluded)<0 or self.datasets_analyzed+self.datasets_excluded!=self.datasets_total:
            raise ValueError('Coverage count mismatch')
        if self.datasets_excluded!=len(self.excluded) or self.partial!=(self.datasets_excluded>0):
            raise ValueError('Exclusion coverage mismatch')
        return self

class SourcePin(BaseModel):
    dataset_id: int
    dataset_name: str
    profile_id: int
    profile_version: int
    state_id: int
    state_version: int
    dataset_version_id: int
    schema_version: int
    suggestion_id: int
    suggestion_version: int

class WorkspaceSuggestion(BaseModel):
    suggestion_id: int
    workspace_id: int
    suggestion_version: int
    algorithm_version: int
    source_pins: list[SourcePin]
    coverage: Coverage
    provider: str
    model: str
    resolved_model: str | None
    created_at: datetime
    completed_at: datetime
    compact_mode: bool
    input_bytes: int
    column_count: int
    reasoning: WorkspaceReasoning
    @model_validator(mode='after')
    def accurate_sources(self):
        ids=[p.dataset_id for p in self.source_pins]
        if len(ids)!=self.coverage.datasets_analyzed or len(set(ids))!=len(ids):
            raise ValueError('Suggestion source coverage mismatch')
        return self

class WorkspaceUnderstanding(BaseModel):
    workspace_id: int
    workspace_name: str
    status: Literal['NOT_GENERATED','STALE','GENERATING','READY','FAILED']
    can_generate: bool
    failure_code: str | None
    readiness_message: str
    coverage: Coverage
    source_pins: list[SourcePin]
    suggestion: WorkspaceSuggestion | None

class WorkspaceSemanticEvidenceBundle(BaseModel):
    """Current deterministic facts plus review-only suggestions, no relationship edges."""
    workspace_id: int
    coverage: Coverage
    profiles: list[SemanticEvidenceBundle]
    dataset_suggestions: list[SuggestionRecord]
    workspace_suggestion: WorkspaceSuggestion
    dataset_roles: list[DatasetRole]
    entity_inventory: list[Entity]
