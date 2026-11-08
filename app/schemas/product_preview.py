"""Safe product read models; never return source locations or artifact bodies."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class CsvPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['quality', 'exact']
    csv_base64: str = Field(strict=True, min_length=1, max_length=2796204)


class Issue(BaseModel):
    column: str
    rule: str
    count: int


class PreviewResponse(BaseModel):
    workspace_id: int
    dataset_id: int
    status: Literal['PREVIEW', 'UNAVAILABLE']
    reason: str | None = None
    rule_version: int | None = None
    total_rows: int | None = None
    valid_rows: int | None = None
    rejected_rows: int | None = None
    columns: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    publication_authorized: Literal[False] = False


class WarehouseChoice(BaseModel):
    model_id: int
    version_id: int
    name: str
    version_number: int


class CapabilitiesResponse(BaseModel):
    workspace_id: int
    dataset_id: int
    csv_upload: bool = True
    xlsx: Literal['UNSUPPORTED'] = 'UNSUPPORTED'
    max_preview_bytes: int = 2097152
    processing_submission: Literal['DISABLED'] = 'DISABLED'
    preview: Literal['REGISTERED_SOURCE_AND_APPROVED_RULES_REQUIRED'] = 'REGISTERED_SOURCE_AND_APPROVED_RULES_REQUIRED'
    warehouse: Literal['NOT_CONFIGURED', 'ACCEPTED_STATE_REQUIRED', 'RECONCILIATION_REQUIRED']
    warehouse_models: list[WarehouseChoice] = Field(default_factory=list)


class JobView(BaseModel):
    job_id: str
    operation: str
    status: str
    attempt_count: int


class JobList(BaseModel):
    workspace_id: int
    dataset_id: int
    jobs: list[JobView]


class GoldAssessmentResponse(BaseModel):
    workspace_id: int
    dataset_id: int
    status: Literal['ASSESSED', 'UNAVAILABLE']
    reason: str | None = None
    rows: int | None = None
    columns: list[str] = Field(default_factory=list)
    publication_authorized: Literal[False] = False
