"""V1 SCD hash domains, reusing 11B's exact typed scalar encoding."""
import hashlib
import json

from app.warehouse.keys import canonical_value
from app.warehouse.scd_types import Code, ScdError, Values


def digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def fields(values: Values):
    try:
        if (not isinstance(values, tuple) or any(not isinstance(p, tuple) or len(p) != 2 for p in values)
                or not all(isinstance(p[0], str) for p in values)
                or len({p[0] for p in values}) != len(values)):
            raise ValueError()
        return [[n, canonical_value(v)] for n, v in sorted(values)]
    except (ValueError, TypeError, UnicodeError):
        raise ScdError(Code.INVALID_INPUT) from None


def attribute_hash(values: Values) -> str:
    return digest(['datarise-warehouse-tracked-attributes', 1, fields(values)])


def context_payload(context):
    return [context.model_id, context.model_version_id, str(context.namespace), context.key_algorithm_version,
            context.configuration.model_dump(mode='json'), context.source_schema.model_dump(mode='json'),
            [context.source_contract.dataset_id, context.source_contract.dataset_version_id,
             context.source_contract.policy_id, context.source_contract.rule_version]]


def order_payload(order):
    return [str(order.domain), order.position] if order else None


def event_digest(event):
    return digest(['datarise-warehouse-scd-event', 1, context_payload(event.context), fields(event.values),
                   str(event.event_id) if event.event_id else None, order_payload(event.order)])


def state_digest(state) -> str:
    if state is None:
        return digest(['datarise-warehouse-scd-state', 1, None])
    rows = [[v.member_sk, v.version_sk, canonical_value(v.valid_from), canonical_value(v.valid_to),
             v.is_current, fields(v.tracked), fields(v.nonhistorical), v.tracked_hash] for v in state.versions]
    return digest(['datarise-warehouse-scd-state', 1, context_payload(state.context), fields(state.business_keys),
                   state.member_sk, rows, canonical_value(state.observed_time), order_payload(state.order),
                   str(state.last_event_id) if state.last_event_id else None, state.last_event_digest])


def changed(before: Values, after: Values) -> tuple[str, ...]:
    old = dict(fields(before))
    return tuple(n for n, value in fields(after) if old.get(n) != value)
