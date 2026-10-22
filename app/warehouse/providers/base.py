"""Backend-only provider scope, bounded policy and safe failure vocabulary."""
from dataclasses import dataclass
from enum import StrEnum
import os
import re
from typing import Protocol


class ErrorCode(StrEnum):
    AUTHENTICATION_FAILED = 'AUTHENTICATION_FAILED'
    PERMISSION_DENIED = 'PERMISSION_DENIED'
    LOCATION_MISMATCH = 'LOCATION_MISMATCH'
    RESOURCE_NOT_FOUND = 'RESOURCE_NOT_FOUND'
    SCHEMA_MISMATCH = 'SCHEMA_MISMATCH'
    RATE_LIMITED = 'RATE_LIMITED'
    QUOTA_EXCEEDED = 'QUOTA_EXCEEDED'
    JOB_CONFLICT = 'JOB_CONFLICT'
    RETRYABLE_PROVIDER_ERROR = 'RETRYABLE_PROVIDER_ERROR'
    PERMANENT_PROVIDER_ERROR = 'PERMANENT_PROVIDER_ERROR'
    TARGET_INCONSISTENT = 'TARGET_INCONSISTENT'
    COST_LIMIT_EXCEEDED = 'COST_LIMIT_EXCEEDED'
    DISABLED = 'DISABLED'


class ProviderError(ValueError):
    def __init__(self, code, *, uncertain=False):
        self.code = ErrorCode(code)
        self.uncertain = uncertain
        super().__init__(self.code.value)  # Never include provider exception text.


class TokenState(StrEnum):
    NOT_APPLIED = 'NOT_APPLIED'
    IN_PROGRESS = 'IN_PROGRESS'
    APPLIED = 'APPLIED'
    INCONSISTENT = 'INCONSISTENT'


@dataclass(frozen=True)
class Policy:
    project: str
    enabled: bool = False
    location: str = 'asia-south1'
    maximum_bytes: int = 1 << 30
    timeout_seconds: int = 60
    maximum_rows: int = 1000
    maximum_payload_bytes: int = 8 << 20

    def __post_init__(self):
        if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', self.project):
            raise ProviderError(ErrorCode.RESOURCE_NOT_FOUND)
        if self.location != 'asia-south1':
            raise ProviderError(ErrorCode.LOCATION_MISMATCH)
        if (not 1 <= self.maximum_bytes <= 1 << 30 or not 1 <= self.timeout_seconds <= 300
                or not 1 <= self.maximum_rows <= 1000 or not 1 <= self.maximum_payload_bytes <= 8 << 20):
            raise ValueError('Invalid bounded provider policy')

    @classmethod
    def from_environment(cls):
        if os.getenv('BIGQUERY_ENABLED', 'false') != 'true':
            raise ProviderError(ErrorCode.DISABLED)
        return cls(project=os.getenv('BIGQUERY_PROJECT_ID', ''), enabled=True,
                   location=os.getenv('BIGQUERY_LOCATION', 'asia-south1'),
                   maximum_bytes=int(os.getenv('BIGQUERY_MAXIMUM_BYTES_BILLED', str(1 << 30))),
                   timeout_seconds=int(os.getenv('BIGQUERY_TIMEOUT_SECONDS', '60')))


@dataclass(frozen=True)
class Binding:
    project: str
    workspace_id: int
    model_id: int
    version_id: int
    resource_id: int
    generation: int
    environment: str = 'development'
    location: str = 'asia-south1'

    def __post_init__(self):
        Policy(self.project, location=self.location)
        if self.environment != 'development':
            raise ValueError('11F provisioning/execution is development-only')
        if any(type(x) is not int or x < 1 for x in (
                self.workspace_id, self.model_id, self.version_id, self.resource_id, self.generation)):
            raise ValueError('Positive owned metadata IDs required')

    @property
    def serving(self):
        return f'dr_dev_w{self.workspace_id}'

    @property
    def staging(self):
        return f'dr_dev_stage_w{self.workspace_id}'

    @property
    def table(self):
        return f'm{self.model_id}_v{self.version_id}_g{self.generation}'

    def physical(self, suffix, *, serving=False):
        if not re.fullmatch(r'[a-z0-9_]{1,100}', suffix):
            raise ValueError('Backend-generated suffix required')
        return f'{self.project}.{self.serving if serving else self.staging}.{self.table}_{suffix}'


class Target(Protocol):
    def lookup(self, batch): ...
    def apply(self, batch): ...
    def reconcile(self, batch): ...
