"""Shared preparation and deterministic plan construction for the two planners."""
from uuid import UUID

from app.warehouse.keys import surrogate_key
from app.warehouse.scd_hashing import attribute_hash, changed, event_digest, state_digest
from app.warehouse.scd_invariants import time_value, validate_timeline, validate_values
from app.warehouse.scd_types import (Acceptance, AcceptedOrder, Action, Authority, Code, Decision,
                                     DimensionEvent, MemberState, Mode, Patch, ScdError, TransitionPlan, Version)


def prepare(context, current, event, authority, scd_type):
    if (not isinstance(event, DimensionEvent) or event.context != context
            or context.configuration.scd_type != scd_type
            or current is not None and current.context != context):
        raise ScdError(Code.MODEL_CONFIG_MISMATCH)
    if (not isinstance(authority, Authority) or not isinstance(authority.mode, Mode)
            or not isinstance(event.acceptance, Acceptance)
            or event.event_id is not None and not isinstance(event.event_id, UUID)
            or event.order is not None and not isinstance(event.order, AcceptedOrder)):
        raise ScdError(Code.INVALID_INPUT)
    config = context.configuration
    names = config.business_keys + config.tracked_attributes + config.nonhistorical_attributes
    if config.effective_time:
        names += (config.effective_time.column,)
    validate_values(context, event.values, names)
    values = dict(event.values)
    instant = time_value(context, values[config.effective_time.column]) if config.effective_time else None
    key = surrogate_key(context.namespace, 'DIMENSION_MEMBER', config.business_keys, values)
    if current:
        validate_timeline(current)
        if current.member_sk != key:
            raise ScdError(Code.KEY_MISMATCH)
    exact_retry = (current is not None and event.acceptance == Acceptance.ACCEPTED
                   and event_digest(event) == current.last_event_digest)
    if (authority.mode == Mode.CORRECTION and authority.expected_state_digest != state_digest(current)
            and not exact_retry):
        raise ScdError(Code.PRECONDITION_FAILED)
    tracked = tuple((n, values[n]) for n in sorted(config.tracked_attributes))
    nonhistorical = tuple((n, values[n]) for n in sorted(config.nonhistorical_attributes))
    return key, instant, tracked, nonhistorical


def blocked(context, current, event, key, decision, code):
    return TransitionPlan(context, decision, code, key, event.event_id, state_digest(current), current)


def source_or_retry(context, current, event, key):
    if event.acceptance != Acceptance.ACCEPTED:
        return blocked(context, current, event, key, Decision.SOURCE_REJECTED, Code.SOURCE_NOT_ACCEPTED)
    if current and event.event_id is not None and event.event_id == current.last_event_id:
        if event_digest(event) != current.last_event_digest:
            return blocked(context, current, event, key, Decision.CONFLICT, Code.EVENT_ID_REUSED)
        return blocked(context, current, event, key, Decision.ALREADY_APPLIED, Code.OK)
    if current and event_digest(event) == current.last_event_digest:
        return blocked(context, current, event, key, Decision.ALREADY_APPLIED, Code.OK)
    return None


def ordering(current, event, instant):
    if instant is not None:
        return (instant > current.observed_time) - (instant < current.observed_time)
    if not current.order or not event.order or current.order.domain != event.order.domain:
        return None
    return (event.order.position > current.order.position) - (event.order.position < current.order.position)


def make_version(context, event, key, instant, tracked, nonhistorical):
    scd2 = context.configuration.scd_type == 'SCD_TYPE_2'
    version_key = surrogate_key(context.namespace, 'DIMENSION_VERSION', context.configuration.business_keys,
                                dict(event.values), valid_from=instant) if scd2 else None
    return Version(key, version_key, instant if scd2 else None, None, True,
                   tracked, nonhistorical, attribute_hash(tracked))


def finish(context, current, event, key, instant, versions, patches, decision, tracked, nonhistorical):
    config = context.configuration
    observed = max(current.observed_time, instant) if current and instant is not None else instant
    order = event.order
    if current and current.order and (not order or current.order.domain == order.domain and current.order.position > order.position):
        order = current.order
    result = MemberState(context, tuple((n, dict(event.values)[n]) for n in sorted(config.business_keys)), key,
                         versions, observed, order, event.event_id, event_digest(event))
    if not patches and result != current:
        patches = (Patch(Action.OBSERVE, key, versions[-1].version_sk),)
    validate_timeline(result)
    old = current.versions[-1] if current else None
    return TransitionPlan(context, decision, Code.OK, key, event.event_id, state_digest(current), result, patches,
                          changed(old.tracked if old else (), tracked),
                          changed(old.nonhistorical if old else (), nonhistorical))
