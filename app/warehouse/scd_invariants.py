"""Reject malformed typed evidence and corrupted member timelines; never repair."""
from datetime import date, datetime, timezone
from decimal import Decimal
import re
from uuid import UUID

from app.warehouse.keys import surrogate_key
from app.warehouse.scd_hashing import attribute_hash, fields
from app.warehouse.scd_types import (AcceptedOrder, Code, MemberState, ScdError, Version)


def validate_values(context, values, names):
    fields(values)  # Also rejects mutable/nested/float values and duplicate names.
    if {n for n, _ in values} != set(names):
        raise ScdError(Code.INVALID_INPUT)
    schema = {c.name: c for c in context.source_schema.columns}
    for name, value in values:
        column = schema[name]
        if value is None:
            if column.nullable is False or name in context.configuration.business_keys:
                raise ScdError(Code.INVALID_INPUT)
            continue
        valid = {'STRING': type(value) is str, 'INTEGER': type(value) is int,
                 'BOOLEAN': type(value) is bool, 'DECIMAL': type(value) is Decimal,
                 'DATE': type(value) is date, 'TIMESTAMP': type(value) is datetime}
        if not valid.get(column.data_type, False):
            raise ScdError(Code.INVALID_INPUT)
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ScdError(Code.INVALID_INPUT)


def time_value(context, value):
    policy = context.configuration.effective_time
    if not policy:
        if value is not None:
            raise ScdError(Code.INVALID_INPUT)
        return None
    validate_values(context, ((policy.column, value),), (policy.column,))
    if value is None:
        raise ScdError(Code.INVALID_INPUT)
    return value.astimezone(timezone.utc) if isinstance(value, datetime) else value


def validate_timeline(state: MemberState) -> None:
    if (not isinstance(state, MemberState) or not isinstance(state.versions, tuple) or not state.versions
            or state.order is not None and not isinstance(state.order, AcceptedOrder)):
        raise ScdError(Code.CORRUPT_TIMELINE)
    context, config = state.context, state.context.configuration
    validate_values(context, state.business_keys, config.business_keys)
    expected_member = surrogate_key(context.namespace, 'DIMENSION_MEMBER', config.business_keys, dict(state.business_keys))
    if state.member_sk != expected_member:
        raise ScdError(Code.KEY_MISMATCH)
    observed = time_value(context, state.observed_time)
    if not config.effective_time and state.observed_time is not None:
        raise ScdError(Code.CORRUPT_TIMELINE)
    if (not isinstance(state.last_event_digest, str) or not re.fullmatch('[a-f0-9]{64}', state.last_event_digest)
            or state.last_event_id is not None and not isinstance(state.last_event_id, UUID)):
        raise ScdError(Code.CORRUPT_TIMELINE)
    seen_keys = set()
    previous = None
    for row in state.versions:
        if not isinstance(row, Version) or type(row.is_current) is not bool:
            raise ScdError(Code.CORRUPT_TIMELINE)
        if row.member_sk != state.member_sk:
            raise ScdError(Code.KEY_MISMATCH)
        validate_values(context, row.tracked, config.tracked_attributes)
        validate_values(context, row.nonhistorical, config.nonhistorical_attributes)
        if row.tracked_hash != attribute_hash(row.tracked):
            raise ScdError(Code.CORRUPT_TIMELINE)
        if config.scd_type == 'SCD_TYPE_1':
            if (len(state.versions) != 1 or row.version_sk is not None or row.valid_from is not None
                    or row.valid_to is not None or not row.is_current):
                raise ScdError(Code.CORRUPT_TIMELINE)
            continue
        start = time_value(context, row.valid_from)
        end = time_value(context, row.valid_to) if row.valid_to is not None else None
        expected = surrogate_key(context.namespace, 'DIMENSION_VERSION', config.business_keys,
                                 dict(state.business_keys), valid_from=start)
        if row.version_sk != expected:
            raise ScdError(Code.KEY_MISMATCH)
        if (row.version_sk in seen_keys or row.is_current != (end is None)
                or end is not None and end <= start or observed < start):
            raise ScdError(Code.CORRUPT_TIMELINE)
        if previous is not None:
            if previous.valid_from >= start or previous.valid_to is None or previous.valid_to > start:
                raise ScdError(Code.CORRUPT_TIMELINE)
        seen_keys.add(row.version_sk)
        previous = row


def version_at(state, business_time):
    """Half-open lookup for validation/reference use; no fact loading or inference."""
    validate_timeline(state)
    if state.context.configuration.scd_type != 'SCD_TYPE_2':
        raise ScdError(Code.MODEL_CONFIG_MISMATCH)
    instant = time_value(state.context, business_time)
    return next((v for v in state.versions if v.valid_from <= instant
                 and (v.valid_to is None or instant < v.valid_to)), None)
