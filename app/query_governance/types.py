"""Pure query-governance value objects. No database, provider or cloud access."""

from dataclasses import dataclass
from enum import StrEnum


class Decision(StrEnum):
    ALLOWED = "ALLOWED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


class EstimateConfidence(StrEnum):
    EXACT = "EXACT"
    UPPER_BOUND = "UPPER_BOUND"
    LOWER_BOUND = "LOWER_BOUND"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class QueryEstimate:
    bytes_processed: int | None
    confidence: EstimateConfidence = EstimateConfidence.EXACT

    def __post_init__(self):
        if self.bytes_processed is not None and self.bytes_processed < 0:
            raise ValueError("Estimated bytes cannot be negative")


@dataclass(frozen=True)
class UsageSnapshot:
    committed_bytes_today: int = 0
    reserved_bytes_today: int = 0

    project_committed_bytes_month: int = 0
    project_reserved_bytes_month: int = 0

    workspace_active_queries: int = 0
    global_active_queries: int = 0

    def __post_init__(self):
        values = (
            self.committed_bytes_today,
            self.reserved_bytes_today,
            self.project_committed_bytes_month,
            self.project_reserved_bytes_month,
            self.workspace_active_queries,
            self.global_active_queries,
        )

        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Usage counters must be non-negative integers")


@dataclass(frozen=True)
class AdmissionDecision:
    decision: Decision
    reason: str
    estimated_bytes: int | None
    maximum_bytes_billed: int
    output_rows: int
    reserved_bytes: int = 0
