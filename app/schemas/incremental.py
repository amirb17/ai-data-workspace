"""Engine-independent incremental policy and application vocabulary."""
from enum import StrEnum
from pydantic import BaseModel, Field, model_validator


class LoadStrategy(StrEnum):
    APPEND = "APPEND"
    UPSERT = "UPSERT"
    SNAPSHOT = "SNAPSHOT"


class RowOutcome(StrEnum):
    INSERTED = "INSERTED"
    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    DEACTIVATED = "DEACTIVATED"


class LoadPolicyRequest(BaseModel):
    dataset_version_id: int = Field(gt=0)
    expected_policy_version: int = Field(ge=0)
    load_strategy: LoadStrategy
    business_keys: list[str] = Field(default_factory=list, max_length=32)
    schema_evolution_policy: str = Field(pattern="^(STRICT|ALLOW_ADDITIVE)$")
    event_time_column: str | None = None
    confirm_policy_change: bool = False

    @model_validator(mode="after")
    def validate_keys(self):
        if len(set(self.business_keys)) != len(self.business_keys) or any(not k or k != k.strip() for k in self.business_keys):
            raise ValueError("Business keys must be distinct ordered column names")
        if self.load_strategy in (LoadStrategy.UPSERT, LoadStrategy.SNAPSHOT) and not self.business_keys:
            raise ValueError("UPSERT and SNAPSHOT require an explicit business key")
        return self


class PrepareApplicationRequest(BaseModel):
    upload_request_id: int = Field(gt=0)
    policy_id: int = Field(gt=0)
