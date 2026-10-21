"""Immutable, provider-neutral reference contracts; no authorization or persistence."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.schemas.warehouse_modeling import DimensionConfiguration, SourceSchema
from app.warehouse.model_validation import validate_model

Scalar = str | int | bool | Decimal | date | datetime | None
Values = tuple[tuple[str, Scalar], ...]
BusinessTime = date | datetime


class Decision(StrEnum):
    INSERT = 'INSERT'
    UPDATE = 'UPDATE'
    UNCHANGED = 'UNCHANGED'
    STALE_REJECTED = 'STALE_REJECTED'
    CONFLICT = 'CONFLICT'
    INSERT_INITIAL_VERSION = 'INSERT_INITIAL_VERSION'
    CLOSE_AND_INSERT = 'CLOSE_AND_INSERT'
    UPDATE_NONHISTORICAL = 'UPDATE_NONHISTORICAL'
    CORRECT_CURRENT = 'CORRECT_CURRENT'
    HISTORICAL_REPAIR_REQUIRED = 'HISTORICAL_REPAIR_REQUIRED'
    ALREADY_APPLIED = 'ALREADY_APPLIED'
    SOURCE_REJECTED = 'SOURCE_REJECTED'


class Code(StrEnum):
    OK = 'OK'
    INVALID_INPUT = 'INVALID_INPUT'
    MODEL_CONFIG_MISMATCH = 'MODEL_CONFIG_MISMATCH'
    KEY_MISMATCH = 'KEY_MISMATCH'
    CORRUPT_TIMELINE = 'CORRUPT_TIMELINE'
    SAME_TIME_CONFLICT = 'SAME_EFFECTIVE_TIME_DIFFERENT_VALUES'
    STALE_SCD1_EVENT = 'STALE_SCD1_EVENT'
    HISTORICAL_REPAIR_REQUIRED = 'HISTORICAL_REPAIR_REQUIRED'
    ORDERING_REQUIRED = 'ORDERING_REQUIRED'
    PRECONDITION_FAILED = 'PRECONDITION_FAILED'
    SOURCE_NOT_ACCEPTED = 'SOURCE_NOT_ACCEPTED'
    EVENT_ID_REUSED = 'EVENT_ID_REUSED'


class ScdError(ValueError):
    def __init__(self, code: Code):
        self.code = code
        super().__init__(code.value)  # Never include business values in errors.


class Mode(StrEnum):
    NORMAL = 'NORMAL'
    CORRECTION = 'CORRECTION'
    BACKFILL = 'BACKFILL'


class Acceptance(StrEnum):
    ACCEPTED = 'ACCEPTED'
    REJECTED = 'REJECTED'
    QUARANTINED = 'QUARANTINED'


class Action(StrEnum):
    INSERT_MEMBER = 'INSERT_MEMBER'
    UPDATE_MEMBER = 'UPDATE_MEMBER'
    INSERT_VERSION = 'INSERT_VERSION'
    CLOSE_VERSION = 'CLOSE_VERSION'
    UPDATE_NONHISTORICAL = 'UPDATE_NONHISTORICAL_ATTRIBUTES'
    CORRECT_CURRENT = 'CORRECT_CURRENT'
    OBSERVE = 'OBSERVE_ACCEPTED_EVENT'


@dataclass(frozen=True)
class SourceContractPin:
    """Logical IDs from the caller's validated 11B configuration snapshot."""
    dataset_id: int
    dataset_version_id: int
    policy_id: int
    rule_version: int

    def __post_init__(self):
        if any(type(n) is not int or n < 1 for n in (
                self.dataset_id, self.dataset_version_id, self.policy_id, self.rule_version)):
            raise ScdError(Code.MODEL_CONFIG_MISMATCH)


@dataclass(frozen=True)
class ModelContext:
    model_id: int
    model_version_id: int
    namespace: UUID
    configuration: DimensionConfiguration
    source_schema: SourceSchema
    source_contract: SourceContractPin
    key_algorithm_version: int = 1

    def __post_init__(self):
        if (type(self.model_id) is not int or self.model_id < 1
                or type(self.model_version_id) is not int or self.model_version_id < 1
                or not isinstance(self.namespace, UUID) or type(self.key_algorithm_version) is not int
                or self.key_algorithm_version != 1 or not isinstance(self.configuration, DimensionConfiguration)
                or not isinstance(self.source_contract, SourceContractPin)):
            raise ScdError(Code.MODEL_CONFIG_MISMATCH)
        try:
            validate_model(self.configuration, self.source_schema)
        except (ValueError, AttributeError):
            raise ScdError(Code.MODEL_CONFIG_MISMATCH) from None


@dataclass(frozen=True)
class AcceptedOrder:
    """Caller-attested immutable source order, never list position or wall clock."""
    domain: UUID
    position: int

    def __post_init__(self):
        if not isinstance(self.domain, UUID) or type(self.position) is not int or self.position < 0:
            raise ScdError(Code.INVALID_INPUT)


@dataclass(frozen=True)
class Authority:
    mode: Mode = Mode.NORMAL
    expected_state_digest: str | None = None


@dataclass(frozen=True)
class DimensionEvent:
    context: ModelContext
    values: Values
    acceptance: Acceptance
    event_id: UUID | None = None
    order: AcceptedOrder | None = None

    @classmethod
    def from_mapping(cls, context, values, *, acceptance, event_id=None, order=None):
        config = context.configuration
        names = set(config.business_keys + config.tracked_attributes + config.nonhistorical_attributes)
        if config.effective_time:
            names.add(config.effective_time.column)
        if not names <= values.keys():
            raise ScdError(Code.INVALID_INPUT)
        # Drop undeclared ingestion/physical metadata, never propagate it in plans.
        return cls(context, tuple((n, values[n]) for n in sorted(names)), acceptance, event_id, order)


@dataclass(frozen=True)
class Version:
    member_sk: str
    version_sk: str | None
    valid_from: BusinessTime | None
    valid_to: BusinessTime | None
    is_current: bool
    tracked: Values
    nonhistorical: Values
    tracked_hash: str


@dataclass(frozen=True)
class MemberState:
    context: ModelContext
    business_keys: Values
    member_sk: str
    versions: tuple[Version, ...]
    observed_time: BusinessTime | None = None
    order: AcceptedOrder | None = None
    last_event_id: UUID | None = None
    last_event_digest: str | None = None


@dataclass(frozen=True)
class Patch:
    action: Action
    member_sk: str
    version_sk: str | None
    before: Version | None = None
    after: Version | None = None


@dataclass(frozen=True)
class TransitionPlan:
    context: ModelContext
    decision: Decision
    code: Code
    member_sk: str
    event_id: UUID | None
    expected_state_digest: str
    result: MemberState | None
    patches: tuple[Patch, ...] = ()
    changed_tracked: tuple[str, ...] = ()
    changed_nonhistorical: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApplyResult:
    decision: Decision
    state: MemberState | None
