"""Safe versioned Phase 7B handoff contract. No raw samples or internal paths."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field


class ColumnEvidence(BaseModel):
    model_config=ConfigDict(extra='forbid')
    original_name:str
    normalized_name:str
    tokens:list[str]
    canonical_type:str
    physical_type:str
    nullable:bool|None
    nullable_basis:Literal['observed_current_state']
    approved_required:bool
    row_count:int
    null_count:int
    non_null_count:int
    null_ratio:float|None
    distinct_count:int
    distinct_ratio:float|None
    cardinality_method:Literal['EXACT']
    identifier_candidate:bool
    identifier_score_components:dict
    authoritative_business_key:bool
    business_key_position:int|None
    authoritative_event_time:bool
    datetime_candidate:bool
    numeric_statistics:dict|None
    datetime_statistics:dict|None
    string_statistics:dict|None
    boolean_distribution:dict|None
    categorical_statistics:dict|None
    pattern_hints:list[dict]
    pattern_scan:dict|None=None
    sensitivity_hints:list[dict]
    min_value:int|float|str|None
    max_value:int|float|str|None
    safe_value_policy:Literal['NO_RAW_SAMPLES_OR_CATEGORICAL_LABELS_V1']


class SemanticEvidenceBundle(BaseModel):
    """Current evidence is available only when profile_ready is true."""
    model_config=ConfigDict(extra='forbid')
    workspace_id:int
    dataset_id:int
    dataset_name:str
    freshness:Literal['NOT_PROFILED','STALE','PROFILING','READY','FAILED']
    profile_ready:bool
    current_state_id:int|None
    state_version:int|None
    dataset_version_id:int|None
    schema_version:int|None
    algorithm_version:int
    built_algorithm_version:int|None
    profile_id:int|None
    profile_version:int|None
    profile_timestamp:datetime|None
    built_from_state_id:int|None
    state_updated_at:datetime|None
    can_refresh:bool
    failure_code:str|None
    summary:dict|None=None
    columns:list[ColumnEvidence]=Field(default_factory=list)
