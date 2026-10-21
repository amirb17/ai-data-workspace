"""Separate provisional member handles: no fabricated effective boundaries or attributes."""
from dataclasses import replace
import re

from app.warehouse.keys import surrogate_key
from app.warehouse.scd1 import plan_scd1
from app.warehouse.scd2 import plan_scd2
from app.warehouse.scd_hashing import context_payload, digest, fields, state_digest
from app.warehouse.scd_invariants import validate_timeline, validate_values
from app.warehouse.scd_reference import apply_plan
from app.warehouse.scd_types import (Acceptance, Authority, Code, Decision, InferenceDecision,
                                     InferencePlan, InferredMember, InferredStatus, ScdError)


def inferred_digest(member) -> str:
    return digest(['datarise-warehouse-inferred-member', 1, None if member is None else
                   [context_payload(member.context), fields(member.business_keys), member.member_sk,
                    member.status.value, member.authoritative_state_digest]])


def validate_inferred(member: InferredMember):
    if (not isinstance(member, InferredMember) or not isinstance(member.status, InferredStatus)
            or member.context.configuration.missing_member_policy != 'INFER_MEMBER'):
        raise ScdError(Code.MODEL_CONFIG_MISMATCH)
    validate_values(member.context, member.business_keys, member.context.configuration.business_keys)
    key = surrogate_key(member.context.namespace, 'DIMENSION_MEMBER', member.context.configuration.business_keys,
                        dict(member.business_keys))
    if member.member_sk != key:
        raise ScdError(Code.KEY_MISMATCH)
    if ((member.status == InferredStatus.INFERRED and member.authoritative_state_digest is not None)
            or member.status == InferredStatus.RESOLVED and (not isinstance(member.authoritative_state_digest, str)
                or not re.fullmatch('[a-f0-9]{64}', member.authoritative_state_digest))):
        raise ScdError(Code.INVALID_INPUT)


def plan_inference(fact, current=None, *, authoritative=None) -> InferencePlan:
    from app.warehouse.fact_resolution import resolve_fact
    from app.warehouse.scd_types import ResolutionStatus
    if current is not None:
        validate_inferred(current)
    result = resolve_fact(fact, state=authoritative, inferred=current)
    expected = inferred_digest(current)
    if result.status == ResolutionStatus.SOURCE_REJECTED:
        return InferencePlan(InferenceDecision.SOURCE_REJECTED, Code.SOURCE_NOT_ACCEPTED, expected, current)
    if result.status in (ResolutionStatus.INFERRED_REFERENCE, ResolutionStatus.MEMBER_NOT_FOUND) and current is not None:
        return InferencePlan(InferenceDecision.ALREADY_APPLIED, Code.OK, expected, current)
    if result.status != ResolutionStatus.INFERRED_MEMBER_REQUIRED:
        return InferencePlan(InferenceDecision.REJECTED, result.code, expected, current)
    member = InferredMember(fact.context, tuple(sorted(fact.business_keys)), result.member_sk)
    validate_inferred(member)
    return InferencePlan(InferenceDecision.CREATED, Code.OK, expected, member)


def plan_completion(inferred, event, authoritative=None, authority=Authority()) -> InferencePlan:
    validate_inferred(inferred)
    if event.context != inferred.context or authoritative is not None and authoritative.context != inferred.context:
        raise ScdError(Code.MODEL_CONFIG_MISMATCH)
    context = inferred.context
    planner = plan_scd1 if context.configuration.scd_type == 'SCD_TYPE_1' else plan_scd2
    transition = planner(context, authoritative, event, authority)
    if transition.member_sk != inferred.member_sk:
        raise ScdError(Code.KEY_MISMATCH)
    expected = inferred_digest(inferred)
    if transition.decision == Decision.SOURCE_REJECTED:
        return InferencePlan(InferenceDecision.SOURCE_REJECTED, Code.SOURCE_NOT_ACCEPTED, expected, inferred)
    if transition.result is None or transition.decision in (Decision.CONFLICT, Decision.STALE_REJECTED,
                                                           Decision.HISTORICAL_REPAIR_REQUIRED):
        return InferencePlan(InferenceDecision.REJECTED, transition.code, expected, inferred)
    if inferred.status == InferredStatus.RESOLVED:
        # Completion cannot be used as an alternate dimension update path.
        if authoritative is None or inferred.authoritative_state_digest != state_digest(authoritative):
            raise ScdError(Code.PRECONDITION_FAILED)
        if transition.patches:
            raise ScdError(Code.PRECONDITION_FAILED)
        return InferencePlan(InferenceDecision.ALREADY_APPLIED, Code.OK, expected, inferred, transition)
    completed = replace(inferred, status=InferredStatus.RESOLVED,
                        authoritative_state_digest=state_digest(transition.result))
    return InferencePlan(InferenceDecision.COMPLETED, Code.OK, expected, completed, transition)


def apply_inference(current, plan: InferencePlan):
    if current is not None:
        validate_inferred(current)
    if plan.result is not None:
        validate_inferred(plan.result)
    if plan.authoritative is not None:
        raise ScdError(Code.INVALID_INPUT)  # Use atomic completion below.
    if plan.decision == InferenceDecision.CREATED:
        if plan.result is None or plan.result.status != InferredStatus.INFERRED:
            raise ScdError(Code.INVALID_INPUT)
        if current is not None and inferred_digest(current) != inferred_digest(plan.result):
            raise ScdError(Code.PRECONDITION_FAILED)
    elif (plan.decision not in (InferenceDecision.REJECTED, InferenceDecision.SOURCE_REJECTED,
                               InferenceDecision.ALREADY_APPLIED)
          or inferred_digest(current) != inferred_digest(plan.result)):
        raise ScdError(Code.PRECONDITION_FAILED)
    if inferred_digest(current) == inferred_digest(plan.result):
        return current
    if inferred_digest(current) != plan.expected_inferred_digest:
        raise ScdError(Code.PRECONDITION_FAILED)
    return plan.result


def apply_completion(inferred, authoritative, plan: InferencePlan):
    if plan.decision in (InferenceDecision.REJECTED, InferenceDecision.SOURCE_REJECTED):
        if inferred_digest(inferred) != plan.expected_inferred_digest:
            raise ScdError(Code.PRECONDITION_FAILED)
        return inferred, authoritative
    if plan.authoritative is None or plan.result is None:
        raise ScdError(Code.INVALID_INPUT)
    validate_inferred(inferred)
    validate_inferred(plan.result)
    if (plan.result.status != InferredStatus.RESOLVED or plan.result.member_sk != inferred.member_sk
            or plan.result.context != inferred.context
            or plan.result.authoritative_state_digest != state_digest(plan.authoritative.result)):
        raise ScdError(Code.PRECONDITION_FAILED)
    if inferred_digest(inferred) not in (plan.expected_inferred_digest, inferred_digest(plan.result)):
        raise ScdError(Code.PRECONDITION_FAILED)
    result = apply_plan(authoritative, plan.authoritative).state
    validate_timeline(result)
    return plan.result, result
