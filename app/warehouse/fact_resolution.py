"""Pinned per-relationship reference lookup only; no fact loading or persistence."""
from datetime import date, datetime, timezone
import re
from uuid import UUID

from app.warehouse.keys import canonical_value, surrogate_key
from app.warehouse.scd_hashing import context_payload, digest, fields, state_digest
from app.warehouse.scd_invariants import time_value, validate_timeline, validate_values
from app.warehouse.scd_types import (Acceptance, Code, FactReference, InferredStatus, ResolutionResult,
                                     ResolutionStatus, ScdError, SourceContractPin)


def validate_fact(fact: FactReference):
    if (not isinstance(fact, FactReference) or not isinstance(fact.fact_sk, str)
            or not re.fullmatch('[a-f0-9]{64}', fact.fact_sk)
            or not isinstance(fact.relationship_id, UUID) or not isinstance(fact.acceptance, Acceptance)
            or not isinstance(fact.source_contract, SourceContractPin)
            or fact.fact_event_id is not None and not isinstance(fact.fact_event_id, UUID)):
        raise ScdError(Code.INVALID_INPUT)
    if fact.context.configuration.scd_type == 'SCD_TYPE_2':
        return time_value(fact.context, fact.event_time)
    if type(fact.event_time) not in (date, datetime):
        raise ScdError(Code.INVALID_INPUT)
    if isinstance(fact.event_time, datetime):
        if fact.event_time.tzinfo is None or fact.event_time.utcoffset() is None:
            raise ScdError(Code.INVALID_INPUT)
        return fact.event_time.astimezone(timezone.utc)
    return fact.event_time


def fact_payload(fact):
    try:
        keys = fields(fact.business_keys)
    except ScdError:
        keys = ['invalid-business-key']  # Stable failure evidence, never serialize raw unsupported input.
    return [fact.fact_sk, str(fact.relationship_id), context_payload(fact.context), keys,
            canonical_value(fact.event_time), fact.acceptance.value, str(fact.fact_event_id) if fact.fact_event_id else None,
            [fact.source_contract.dataset_id, fact.source_contract.dataset_version_id,
             fact.source_contract.policy_id, fact.source_contract.rule_version]]


def resolution_digest(result: ResolutionResult) -> str:
    return digest(['datarise-warehouse-fact-dimension-resolution', 1, fact_payload(result.fact), result.status.value,
                   result.code.value, result.member_sk, result.version_sk,
                   result.dimension_state_digest, result.inferred_digest])


def resolve_fact(fact: FactReference, state=None, inferred=None) -> ResolutionResult:
    instant = validate_fact(fact)
    context = fact.context
    if state is not None and state.context != context or inferred is not None and inferred.context != context:
        raise ScdError(Code.MODEL_CONFIG_MISMATCH)

    def result(status, code=Code.OK, member=None, version=None, inferred_hash=None):
        return ResolutionResult(fact, status, code, member, version,
                                state_digest(state) if state is not None else state_digest(None), inferred_hash)

    if fact.acceptance != Acceptance.ACCEPTED:
        # Never inspect/rehabilitate a rejected fact's dimension lookup.
        return ResolutionResult(fact, ResolutionStatus.SOURCE_REJECTED, Code.SOURCE_NOT_ACCEPTED,
                                None, None, state_digest(None))
    try:
        validate_values(context, fact.business_keys, context.configuration.business_keys)
        member = surrogate_key(context.namespace, 'DIMENSION_MEMBER', context.configuration.business_keys,
                               dict(fact.business_keys))
    except (ScdError, ValueError):
        return ResolutionResult(fact, ResolutionStatus.INVALID_BUSINESS_KEY, Code.INVALID_INPUT,
                                None, None, state_digest(None))
    if inferred is not None:
        from app.warehouse.inferred_members import inferred_digest, validate_inferred
        validate_inferred(inferred)
        if inferred.member_sk != member:
            raise ScdError(Code.KEY_MISMATCH)
    if state is None:
        if inferred is not None:
            if inferred.status == InferredStatus.INFERRED:
                return result(ResolutionStatus.INFERRED_REFERENCE, member=member, inferred_hash=inferred_digest(inferred))
            return result(ResolutionStatus.MEMBER_NOT_FOUND, Code.PRECONDITION_FAILED, member)
        status = (ResolutionStatus.INFERRED_MEMBER_REQUIRED if context.configuration.missing_member_policy == 'INFER_MEMBER'
                  else ResolutionStatus.MEMBER_NOT_FOUND)
        return result(status, member=member)
    try:
        validate_timeline(state)
    except ScdError as error:
        # Corruption is an explicit failure, never a multiple-row or NULL success.
        status = ResolutionStatus.AMBIGUOUS_HISTORY if error.code == Code.CORRUPT_TIMELINE else ResolutionStatus.INVALID_DIMENSION_STATE
        return ResolutionResult(fact, status, error.code, member, None, state_digest(None))
    if state.member_sk != member:
        raise ScdError(Code.KEY_MISMATCH)
    if context.configuration.scd_type == 'SCD_TYPE_1':
        return result(ResolutionStatus.CURRENT_MEMBER_ONLY, member=member)
    matches = [v for v in state.versions if v.valid_from <= instant and (v.valid_to is None or instant < v.valid_to)]
    if len(matches) > 1:
        return result(ResolutionStatus.AMBIGUOUS_HISTORY, Code.CORRUPT_TIMELINE, member)
    if not matches:
        return result(ResolutionStatus.NO_VERSION_AT_EVENT_TIME, member=member)
    return result(ResolutionStatus.RESOLVED, member=member, version=matches[0].version_sk)
