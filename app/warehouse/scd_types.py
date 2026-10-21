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
    HISTORICAL_INSERT = 'HISTORICAL_INSERT'
    HISTORICAL_SPLIT = 'HISTORICAL_SPLIT'
    HISTORICAL_REPLACE = 'HISTORICAL_REPLACE'
    GAP_INSERT = 'GAP_INSERT'
    BEFORE_HISTORY_REJECTED = 'BEFORE_HISTORY_REJECTED'
    REPAIR_NOT_AUTHORIZED = 'REPAIR_NOT_AUTHORIZED'
    DEFERRED_NONHISTORICAL = 'DEFERRED_NONHISTORICAL'


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
    OBSERVATION_COVERAGE_REQUIRED = 'OBSERVATION_COVERAGE_REQUIRED'


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
    SHORTEN_INTERVAL = 'SHORTEN_INTERVAL'
    INSERT_HISTORICAL = 'INSERT_HISTORICAL_VERSION'
    CORRECT_HISTORICAL = 'CORRECT_HISTORICAL_VERSION'


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


class BeforeHistoryPolicy(StrEnum):
    EXTEND_FROM_ACCEPTED_EVIDENCE = 'EXTEND_FROM_ACCEPTED_EVIDENCE'
    REJECT = 'REJECT'


@dataclass(frozen=True)
class TimeRange:
    valid_from: BusinessTime
    valid_to: BusinessTime | None


@dataclass(frozen=True)
class HistoricalRepairPlan:
    transition: TransitionPlan
    affected_ranges: tuple[TimeRange, ...]
    event_digest: str
    earliest_authoritative_valid_from: BusinessTime | None


class InferredStatus(StrEnum):
    INFERRED = 'INFERRED'
    RESOLVED = 'RESOLVED'


class InferenceDecision(StrEnum):
    CREATED = 'CREATED'
    ALREADY_APPLIED = 'ALREADY_APPLIED'
    COMPLETED = 'COMPLETED'
    REJECTED = 'REJECTED'
    SOURCE_REJECTED = 'SOURCE_REJECTED'


@dataclass(frozen=True)
class InferredMember:
    context: ModelContext
    business_keys: Values
    member_sk: str
    status: InferredStatus = InferredStatus.INFERRED
    authoritative_state_digest: str | None = None


@dataclass(frozen=True)
class InferencePlan:
    decision: InferenceDecision
    code: Code
    expected_inferred_digest: str
    result: InferredMember | None
    authoritative: TransitionPlan | None = None


@dataclass(frozen=True)
class FactReference:
    """An existing logical fact identity and one dimension relationship; no measures."""
    fact_sk: str
    relationship_id: UUID
    context: ModelContext
    business_keys: Values
    event_time: BusinessTime
    acceptance: Acceptance
    source_contract: SourceContractPin
    fact_event_id: UUID | None = None

    @classmethod
    def from_mapping(cls, fact_sk, relationship_id, context, keys, event_time, *, acceptance,
                     source_contract, fact_event_id=None):
        return cls(fact_sk, relationship_id, context,
                   tuple((n, keys.get(n)) for n in sorted(context.configuration.business_keys)),
                   event_time, acceptance, source_contract, fact_event_id)


class ResolutionStatus(StrEnum):
    RESOLVED = 'RESOLVED'
    CURRENT_MEMBER_ONLY = 'CURRENT_MEMBER_ONLY'
    INFERRED_MEMBER_REQUIRED = 'INFERRED_MEMBER_REQUIRED'
    INFERRED_REFERENCE = 'INFERRED_REFERENCE'
    MEMBER_NOT_FOUND = 'MEMBER_NOT_FOUND'
    NO_VERSION_AT_EVENT_TIME = 'NO_VERSION_AT_EVENT_TIME'
    AMBIGUOUS_HISTORY = 'AMBIGUOUS_HISTORY'
    INVALID_DIMENSION_STATE = 'INVALID_DIMENSION_STATE'
    INVALID_BUSINESS_KEY = 'INVALID_BUSINESS_KEY'
    SOURCE_REJECTED = 'SOURCE_REJECTED'


@dataclass(frozen=True)
class ResolutionResult:
    fact: FactReference
    status: ResolutionStatus
    code: Code
    member_sk: str | None
    version_sk: str | None
    dimension_state_digest: str
    inferred_digest: str | None = None


class RebindDecision(StrEnum):
    UNCHANGED = 'UNCHANGED'
    REBIND = 'REBIND'
    UNRESOLVED = 'UNRESOLVED'


class RebindReason(StrEnum):
    OUTSIDE_AFFECTED_RANGE = 'OUTSIDE_AFFECTED_RANGE'
    IDENTITY_STABLE = 'IDENTITY_STABLE'
    VERSION_CHANGED = 'VERSION_CHANGED'
    COVERAGE_UNRESOLVED = 'COVERAGE_UNRESOLVED'


@dataclass(frozen=True)
class FactRebind:
    decision: RebindDecision
    reason: RebindReason
    before: ResolutionResult
    after: ResolutionResult
    expected_previous_resolution_digest: str


@dataclass(frozen=True)
class FactReResolutionPlan:
    context: ModelContext
    before: MemberState | None
    after: MemberState | None
    before_inferred: InferredMember | None
    after_inferred: InferredMember | None
    affected_ranges: tuple[TimeRange, ...]
    entries: tuple[FactRebind, ...]
