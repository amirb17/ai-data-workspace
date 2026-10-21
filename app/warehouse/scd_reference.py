"""Bounded in-memory compare-and-apply reference, not a persistence engine."""
from dataclasses import replace
from app.warehouse.scd_hashing import state_digest
from app.warehouse.scd_invariants import validate_timeline
from app.warehouse.scd_types import Action, ApplyResult, Code, Decision, ScdError


def apply_plan(current, plan) -> ApplyResult:
    if current is not None:
        validate_timeline(current)
        if current.context != plan.context or current.member_sk != plan.member_sk:
            raise ScdError(Code.MODEL_CONFIG_MISMATCH)
    if plan.result is not None:
        validate_timeline(plan.result)
        if plan.result.context != plan.context or plan.result.member_sk != plan.member_sk:
            raise ScdError(Code.MODEL_CONFIG_MISMATCH)
    if state_digest(current) == state_digest(plan.result):
        return ApplyResult(Decision.ALREADY_APPLIED if plan.patches else plan.decision, current)
    if state_digest(current) != plan.expected_state_digest:
        raise ScdError(Code.PRECONDITION_FAILED)
    if not plan.patches or plan.result is None:
        raise ScdError(Code.PRECONDITION_FAILED)
    if current:
        if (current.observed_time is not None and plan.result.observed_time < current.observed_time
                or current.order is not None and plan.result.order is not None
                and current.order.domain == plan.result.order.domain
                and plan.result.order.position < current.order.position):
            raise ScdError(Code.PRECONDITION_FAILED)
    rows = list(current.versions) if current else []
    for patch in plan.patches:
        if patch.member_sk != plan.member_sk:
            raise ScdError(Code.KEY_MISMATCH)
        if patch.action == Action.OBSERVE:
            if (patch.before is not None or patch.after is not None or len(plan.patches) != 1 or not rows
                    or patch.version_sk != rows[-1].version_sk):
                raise ScdError(Code.INVALID_INPUT)
            continue
        if patch.after is None or patch.version_sk != patch.after.version_sk:
            raise ScdError(Code.KEY_MISMATCH)
        if patch.action in (Action.INSERT_MEMBER, Action.INSERT_VERSION):
            if patch.before is not None or any(v.version_sk == patch.version_sk for v in rows):
                raise ScdError(Code.PRECONDITION_FAILED)
            expected_action = Action.INSERT_MEMBER if plan.context.configuration.scd_type == 'SCD_TYPE_1' else Action.INSERT_VERSION
            if patch.action != expected_action:
                raise ScdError(Code.INVALID_INPUT)
            rows.append(patch.after)
        elif patch.action in (Action.UPDATE_MEMBER, Action.CLOSE_VERSION, Action.UPDATE_NONHISTORICAL, Action.CORRECT_CURRENT):
            if patch.before is None or patch.before not in rows:
                raise ScdError(Code.PRECONDITION_FAILED)
            before, after = patch.before, patch.after
            if patch.action == Action.CLOSE_VERSION:
                if (not before.is_current or after.valid_to is None
                        or after != replace(before, valid_to=after.valid_to, is_current=False)):
                    raise ScdError(Code.INVALID_INPUT)
            elif patch.action == Action.UPDATE_NONHISTORICAL:
                if after != replace(before, nonhistorical=after.nonhistorical) or not before.is_current:
                    raise ScdError(Code.INVALID_INPUT)
            elif after != replace(before, tracked=after.tracked, nonhistorical=after.nonhistorical,
                                  tracked_hash=after.tracked_hash) or not before.is_current:
                raise ScdError(Code.INVALID_INPUT)
            if (patch.action == Action.UPDATE_MEMBER) != (plan.context.configuration.scd_type == 'SCD_TYPE_1'):
                raise ScdError(Code.INVALID_INPUT)
            rows[rows.index(patch.before)] = patch.after
        else:
            raise ScdError(Code.INVALID_INPUT)
    if plan.result is None or tuple(rows) != plan.result.versions:
        raise ScdError(Code.PRECONDITION_FAILED)
    return ApplyResult(plan.decision, plan.result)
