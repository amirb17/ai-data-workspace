"""Strict metadata inputs; physical mappings are backend-only contracts."""
from enum import StrEnum
import unicodedata
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MetadataInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ModelCreate(MetadataInput):
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator('display_name')
    @classmethod
    def printable_name(cls, value):
        if any(unicodedata.category(c).startswith('C') for c in value):
            raise ValueError('Model name must contain printable text')
        return value

    @property
    def normalized_name(self):
        return ' '.join(unicodedata.normalize('NFKC', self.display_name).casefold().split())


class VersionCreate(MetadataInput):
    expected_version: int = Field(ge=0)
    configuration_version: Literal[1] = 1
    key_algorithm_version: Literal[1] = 1
    key_storage_type: Literal['STRING'] = 'STRING'
    key_strategy: Literal['DETERMINISTIC'] = 'DETERMINISTIC'


class SourceMapping(MetadataInput):
    source_dataset_id: int = Field(gt=0)
    purpose: Literal['PRIMARY', 'DEPENDENCY'] = 'PRIMARY'


class BigQueryReference(MetadataInput):
    """Internal registration, never a frontend request or public response."""
    reference_version: Literal[1] = 1
    project: str = Field(pattern=r'^[a-z][a-z0-9-]{4,28}[a-z0-9]$')
    dataset: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,1023}$')
    table: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,1023}$')


class ResourceRegistration(MetadataInput):
    provider: Literal['BIGQUERY'] = 'BIGQUERY'
    environment: Literal['development', 'production']
    location: str = Field(pattern=r'^[a-z][a-z0-9-]{1,62}$')
    generation: int = Field(gt=0)
    physical_reference: BigQueryReference

    @model_validator(mode='after')
    def production_location(self):
        if self.environment == 'production' and self.location != 'asia-south1':
            raise ValueError('Production warehouse location must be asia-south1')
        return self


class OperationType(StrEnum):
    INITIAL_LOAD = 'INITIAL_LOAD'
    INCREMENTAL = 'INCREMENTAL'
    REPLAY = 'REPLAY'
    BACKFILL = 'BACKFILL'
    REBUILD = 'REBUILD'


class OperationCreate(MetadataInput):
    operation_type: OperationType
    resource_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=1000)


class RunStatus(StrEnum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'


class RunTransition(MetadataInput):
    status: RunStatus
    failure_code: str | None = Field(default=None, pattern=r'^[A-Z][A-Z0-9_]{0,63}$')

    @model_validator(mode='after')
    def failure(self):
        if (self.status == RunStatus.FAILED) != (self.failure_code is not None):
            raise ValueError('Only FAILED requires a failure code')
        return self
