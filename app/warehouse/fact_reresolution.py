"""Bounded reference rebinding over actual semantic timeline differences."""
from app.warehouse.fact_resolution import resolve_fact, resolution_digest
from app.warehouse.scd_hashing import changed, context_payload, digest, state_digest
from app.warehouse.scd_invariants import validate_timeline
from app.warehouse.scd_types import (Code, FactRebind, FactReResolutionPlan, RebindDecision,
                                     RebindReason, ResolutionStatus, ScdError, TimeRange)
from app.warehouse.keys import canonical_value


def _validate_states(context, before, after):
    for state in (before, after):
        if state is not None:
            if state.context != context:
                raise ScdError(Code.MODEL_CONFIG_MISMATCH)
            validate_timeline(state)
    if before is not None and after is not None and before.member_sk != after.member_sk:
        raise ScdError(Code.KEY_MISMATCH)


def affected_ranges(context, before, after):
    """Derive ranges; callers cannot hide changes by supplying a too-small range."""
    _validate_states(context, before, after)
    if context.configuration.scd_type == 'SCD_TYPE_1':
        # Untimed current-value semantics have no authoritative as-of range.
        return ()
    old = before.versions if before else ()
    new = after.versions if after else ()
    bounds = sorted({t for row in old + new for t in (row.valid_from, row.valid_to) if t is not None})

    def row_at(rows, instant):
        return next((v for v in rows if v.valid_from <= instant
                     and (v.valid_to is None or instant < v.valid_to)), None)

    result = []
    for i, start in enumerate(bounds):
        end = bounds[i+1] if i+1 < len(bounds) else None
        a, b = row_at(old, start), row_at(new, start)
        same = (a is None and b is None or a is not None and b is not None
                and a.version_sk == b.version_sk and not changed(a.tracked, b.tracked)
                and not changed(a.nonhistorical, b.nonhistorical))
        if not same:
            if result and result[-1].valid_to == start:
                result[-1] = TimeRange(result[-1].valid_from, end)
            else:
                result.append(TimeRange(start, end))
    return tuple(result)


def reresolution_digest(plan):
    from app.warehouse.inferred_members import inferred_digest
    return digest(['datarise-warehouse-fact-reresolution', 1, context_payload(plan.context),
                   state_digest(plan.before), state_digest(plan.after),
                   inferred_digest(plan.before_inferred), inferred_digest(plan.after_inferred),
                   [[canonical_value(r.valid_from), canonical_value(r.valid_to)] for r in plan.affected_ranges],
                   [[e.decision.value, e.reason.value, e.expected_previous_resolution_digest,
                     resolution_digest(e.after)] for e in plan.entries]])


def plan_reresolution(context, before, after, references, *, before_inferred=None, after_inferred=None):
    ranges = affected_ranges(context, before, after)
    from app.warehouse.inferred_members import validate_inferred
    members = {state.member_sk for state in (before, after) if state is not None}
    for inferred in (before_inferred, after_inferred):
        if inferred is not None:
            validate_inferred(inferred)
            if inferred.context != context:
                raise ScdError(Code.MODEL_CONFIG_MISMATCH)
            members.add(inferred.member_sk)
    if len(members) > 1:
        raise ScdError(Code.KEY_MISMATCH)
    references = tuple(references)
    identities = [(r.fact.fact_sk, r.fact.relationship_id) for r in references]
    if len(set(identities)) != len(identities):
        raise ScdError(Code.INVALID_INPUT)
    entries = []
    for previous in sorted(references, key=lambda r: (r.fact.fact_sk, str(r.fact.relationship_id))):
        if previous.fact.context != context:
            raise ScdError(Code.MODEL_CONFIG_MISMATCH)
        expected = resolve_fact(previous.fact, before, before_inferred)
        if resolution_digest(previous) != resolution_digest(expected):
            raise ScdError(Code.PRECONDITION_FAILED)
        instant = previous.fact.event_time
        inside = (context.configuration.scd_type == 'SCD_TYPE_1' or
                  any(r.valid_from <= instant and (r.valid_to is None or instant < r.valid_to) for r in ranges))
        if not inside:
            entries.append(FactRebind(RebindDecision.UNCHANGED, RebindReason.OUTSIDE_AFFECTED_RANGE,
                                     previous, previous, resolution_digest(previous)))
            continue
        result = resolve_fact(previous.fact, after, after_inferred)
        if result.status not in (ResolutionStatus.RESOLVED, ResolutionStatus.CURRENT_MEMBER_ONLY):
            decision, reason = RebindDecision.UNRESOLVED, RebindReason.COVERAGE_UNRESOLVED
        elif (result.version_sk, result.member_sk, result.status) == (previous.version_sk, previous.member_sk, previous.status):
            decision, reason = RebindDecision.UNCHANGED, RebindReason.IDENTITY_STABLE
        else:
            decision, reason = RebindDecision.REBIND, RebindReason.VERSION_CHANGED
        entries.append(FactRebind(decision, reason, previous, result, resolution_digest(previous)))
    return FactReResolutionPlan(context, before, after, before_inferred, after_inferred, ranges, tuple(entries))


def apply_reresolution(references, plan):
    """Atomic compare-and-apply on a bounded tuple; no partial durable recovery."""
    # Re-derive outcomes to reject a forged/tampered after-reference or range.
    expected = plan_reresolution(plan.context, plan.before, plan.after, tuple(e.before for e in plan.entries),
                                 before_inferred=plan.before_inferred, after_inferred=plan.after_inferred)
    if expected != plan:
        raise ScdError(Code.PRECONDITION_FAILED)
    references = tuple(sorted(references, key=lambda r: (r.fact.fact_sk, str(r.fact.relationship_id))))
    if len(references) != len(plan.entries):
        raise ScdError(Code.PRECONDITION_FAILED)
    actual = tuple(resolution_digest(r) for r in references)
    prior = tuple(e.expected_previous_resolution_digest for e in plan.entries)
    result = tuple(e.after for e in plan.entries)
    if actual == tuple(resolution_digest(r) for r in result):
        return result
    if actual != prior:
        raise ScdError(Code.PRECONDITION_FAILED)
    return result
