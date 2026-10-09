"""Provider-independent, strictly bounded suggestions. Never trusted approvals."""
from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Role = Literal['IDENTIFIER','ENTITY_REFERENCE','DIMENSION','MEASURE','TIME_DIMENSION',
    'STATUS','CATEGORY','CODE','BOOLEAN_FLAG','TEXT_ATTRIBUTE','UNKNOWN']
ShortText = Annotated[str, StringConstraints(max_length=300)]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Candidate(Strict):
    label: str | None = Field(max_length=120)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=500)


class Classification(Strict):
    primary: Candidate
    alternatives: list[Candidate] = Field(max_length=3)


class ColumnSuggestion(Strict):
    column_name: str = Field(max_length=256)
    suggested_role: Role
    secondary_hints: list[Role] = Field(max_length=3)
    business_meaning: str = Field(max_length=300)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=500)
    warnings: list[ShortText] = Field(max_length=5)


class SemanticReasoning(Strict):
    domain: Classification
    subdomain: Classification
    entity: Classification
    columns: list[ColumnSuggestion] = Field(max_length=200)
    overall_confidence: float = Field(ge=0, le=1)
    warnings: list[ShortText] = Field(max_length=10)
    unresolved_questions: list[ShortText] = Field(max_length=10)


class GenerateRequest(Strict):
    """No caller-controlled evidence, prompts, profile IDs or model settings."""
    pass


class SuggestionRecord(BaseModel):
    suggestion_id: int
    suggestion_version: int
    workspace_id: int
    dataset_id: int
    source_profile_id: int
    source_state_id: int
    source_state_version: int
    semantic_model_version: int
    provider: str
    model: str
    resolved_model: str | None
    created_at: datetime
    completed_at: datetime
    compact_mode: bool
    input_bytes: int
    reasoning: SemanticReasoning


class Understanding(BaseModel):
    workspace_id: int
    dataset_id: int
    dataset_name: str
    status: Literal['NOT_GENERATED','STALE','GENERATING','READY','FAILED']
    profile_freshness: str
    can_generate: bool
    failure_code: str | None
    source_profile_id: int | None
    source_state_id: int | None
    suggestion: SuggestionRecord | None
    evidence: list[dict] = Field(default_factory=list)
