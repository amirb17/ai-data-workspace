"""Accepted historical SCD2 repair; no storage, source override or inferred history."""
from dataclasses import replace

from app.warehouse.keys import canonical_value
from app.warehouse.scd2 import plan_scd2
from app.warehouse.scd_common import prepare, source_or_retry, blocked, make_version
from app.warehouse.scd_hashing import attribute_hash, changed, digest, event_digest, state_digest
from app.warehouse.scd_invariants import validate_timeline
from app.warehouse.scd_types import (Action, Authority, BeforeHistoryPolicy, Code, Decision,
                                     HistoricalRepairPlan, Mode, Patch, ScdError, TimeRange, TransitionPlan)


def repair_digest(plan: HistoricalRepairPlan) -> str:
    t = plan.transition
    return digest(['datarise-warehouse-historical-repair', 1, t.expected_state_digest, state_digest(t.result),
                   t.decision.value, t.code.value, plan.event_digest,
                   [[p.action.value, p.member_sk, p.version_sk] for p in t.patches],
                   [[canonical_value(r.valid_from), canonical_value(r.valid_to)] for r in plan.affected_ranges]])


def plan_repair(context, current, event, authority=Authority(),
                before_history=BeforeHistoryPolicy.EXTEND_FROM_ACCEPTED_EVIDENCE) -> HistoricalRepairPlan:
    # Validate the evidence/state through 11C, then check repair-specific authority.
    key, instant, tracked, nonhistorical = prepare(context, current, event, Authority(), 'SCD_TYPE_2')
    if (not isinstance(authority, Authority) or not isinstance(authority.mode, Mode)
            or not isinstance(before_history, BeforeHistoryPolicy)):
        raise ScdError(Code.INVALID_INPUT)

    def wrap(transition, ranges=()):
        result = transition.result
        earliest = result.versions[0].valid_from if result else None
        return HistoricalRepairPlan(transition, ranges, event_digest(event), earliest)

    def stop(decision, code=Code.OK):
        return wrap(blocked(context, current, event, key, decision, code))

    early = source_or_retry(context, current, event, key)
    if early:
        return wrap(early)
    if current is None:
        if authority.mode == Mode.CORRECTION and authority.expected_state_digest != state_digest(None):
            return stop(Decision.REPAIR_NOT_AUTHORIZED, Code.PRECONDITION_FAILED)
        transition = plan_scd2(context, None, event, authority)
        return wrap(transition, (TimeRange(instant, None),))
    rows = current.versions
    exact = next((v for v in rows if v.valid_from == instant), None)
    comparison = exact
    if exact and not changed(exact.tracked, tracked) and not changed(exact.nonhistorical, nonhistorical):
        return stop(Decision.ALREADY_APPLIED)
    if authority.mode == Mode.CORRECTION and authority.expected_state_digest != state_digest(current):
        return stop(Decision.REPAIR_NOT_AUTHORIZED, Code.PRECONDITION_FAILED)
    if exact:
        if authority.mode != Mode.CORRECTION:
            if not changed(exact.tracked, tracked):
                return stop(Decision.DEFERRED_NONHISTORICAL)
            return stop(Decision.CONFLICT, Code.SAME_TIME_CONFLICT)
        row = replace(exact, tracked=tracked, nonhistorical=nonhistorical, tracked_hash=attribute_hash(tracked))
        result_rows = tuple(row if v.version_sk == exact.version_sk else v for v in rows)
        patches = (Patch(Action.CORRECT_HISTORICAL, key, exact.version_sk, exact, row),)
        decision, affected = Decision.HISTORICAL_REPLACE, TimeRange(exact.valid_from, exact.valid_to)
    else:
        containing = next((v for v in rows if v.valid_from < instant
                           and (v.valid_to is None or instant < v.valid_to)), None)
        comparison = containing
        if instant > current.observed_time and rows[-1].is_current:
            transition = plan_scd2(context, current, event, authority)
            start = rows[-1].valid_from if transition.decision == Decision.UPDATE_NONHISTORICAL else instant
            ranges = () if transition.decision == Decision.UNCHANGED else (TimeRange(start, None),)
            return wrap(transition, ranges)
        if containing and not changed(containing.tracked, tracked):
            return stop(Decision.DEFERRED_NONHISTORICAL if changed(containing.nonhistorical, nonhistorical)
                        else Decision.UNCHANGED)
        if containing and containing.valid_to is None:
            # The observation watermark can prove later unchanged values but does
            # not retain every observation. Do not rewind current truth guessing
            # missing boundaries from an incomplete local evidence set.
            return stop(Decision.REPAIR_NOT_AUTHORIZED, Code.OBSERVATION_COVERAGE_REQUIRED)
        successor = next((v for v in rows if v.valid_from > instant), None)
        if successor is None:
            return stop(Decision.REPAIR_NOT_AUTHORIZED, Code.HISTORICAL_REPAIR_REQUIRED)
        if instant < rows[0].valid_from and before_history == BeforeHistoryPolicy.REJECT:
            return stop(Decision.BEFORE_HISTORY_REJECTED, Code.HISTORICAL_REPAIR_REQUIRED)
        end = containing.valid_to if containing else successor.valid_from
        row = replace(make_version(context, event, key, instant, tracked, nonhistorical), valid_to=end, is_current=False)
        if containing:
            shortened = replace(containing, valid_to=instant, is_current=False)
            base = tuple(shortened if v.version_sk == containing.version_sk else v for v in rows)
            patches = (Patch(Action.SHORTEN_INTERVAL, key, containing.version_sk, containing, shortened),)
            decision = Decision.HISTORICAL_SPLIT
        else:
            base, patches = rows, ()
            decision = Decision.HISTORICAL_INSERT if instant < rows[0].valid_from else Decision.GAP_INSERT
        result_rows = tuple(sorted(base + (row,), key=lambda v: v.valid_from))
        patches += (Patch(Action.INSERT_HISTORICAL, key, row.version_sk, after=row),)
        affected = TimeRange(instant, end)
    # Late repairs preserve the forward observation receipt/watermark, making
    # equivalent independent repairs insensitive to physical arrival order.
    result = replace(current, versions=result_rows)
    validate_timeline(result)
    transition = TransitionPlan(context, decision, Code.OK, key, event.event_id, state_digest(current), result,
                                patches, changed(comparison.tracked if comparison else (), tracked),
                                changed(comparison.nonhistorical if comparison else (), nonhistorical))
    return wrap(transition, (affected,))
