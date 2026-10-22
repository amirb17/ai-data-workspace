"""Deterministic typed columns and independent physical-member reconstruction."""
from uuid import UUID
from app.warehouse.accepted_changes import digest, projection
from app.warehouse.scd_types import AcceptedOrder, MemberState, Version
from app.warehouse.scd_invariants import validate_timeline
from app.warehouse.providers.base import ErrorCode, ProviderError
from app.warehouse.providers.types import TYPES, decode, encode


def columns(context):
    schema = {c.name: c for c in context.source_schema.columns}
    result = []
    for i, name in enumerate(projection(context.configuration)):
        kind = TYPES.get(schema[name].data_type)
        if kind is None:
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        result.append((name, f'c{i}', kind))
    return tuple(result)


def schema(context):
    business = [(column, kind, 'NULLABLE') for _, column, kind in columns(context)]
    time = context.configuration.effective_time
    time_kind = next((kind for name, _, kind in columns(context) if time and name == time.column), 'TIMESTAMP')
    return tuple([('member_sk', 'STRING', 'REQUIRED'), ('version_sk', 'STRING', 'NULLABLE'),
                  ('valid_from', time_kind, 'NULLABLE'), ('valid_to', time_kind, 'NULLABLE'),
                  ('is_current', 'BOOL', 'REQUIRED'), ('tracked_hash', 'STRING', 'REQUIRED'),
                  ('observed_time', time_kind, 'NULLABLE'), ('order_domain', 'STRING', 'NULLABLE'),
                  ('order_position', 'INT64', 'NULLABLE'), ('last_event_id', 'STRING', 'NULLABLE'),
                  ('last_event_digest', 'STRING', 'REQUIRED')] + business)


def rows(plan):
    result = []
    kinds = {n: t for n, t, _ in schema(plan.context)}
    policy = plan.context.configuration.effective_time
    for state in plan.after:
        validate_timeline(state)
        for version in state.versions:
            row = dict(member_sk=state.member_sk, version_sk=version.version_sk,
                valid_from=encode(version.valid_from, kinds['valid_from']),
                valid_to=encode(version.valid_to, kinds['valid_to']), is_current=version.is_current,
                tracked_hash=version.tracked_hash, observed_time=encode(state.observed_time, kinds['observed_time']),
                order_domain=str(state.order.domain) if state.order else None,
                order_position=state.order.position if state.order else None,
                last_event_id=str(state.last_event_id) if state.last_event_id else None,
                last_event_digest=state.last_event_digest)
            values = dict(state.business_keys + version.tracked + version.nonhistorical)
            if policy:
                values[policy.column] = state.observed_time
            row.update({column: encode(values[name], kind) for name, column, kind in columns(plan.context)})
            result.append(row)
    return result


def states(context, physical_rows):
    kinds = {n: t for n, t, _ in schema(context)}
    grouped = {}
    for incoming in physical_rows:
        row = dict(incoming)
        if set(row) != set(kinds):
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        row = {n: decode(row[n], kind) for n, kind in kinds.items()}
        grouped.setdefault(row['member_sk'], []).append(row)
    result = []
    for member, group in sorted(grouped.items()):
        group.sort(key=lambda row: row['valid_from'] if row['valid_from'] is not None else '')
        first = group[0]
        if (first['order_domain'] is None) != (first['order_position'] is None):
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        values = lambda row, names: tuple(sorted((name, row[column]) for name, column, _ in columns(context) if name in names))
        metadata = ('observed_time', 'order_domain', 'order_position', 'last_event_id', 'last_event_digest')
        versions = []
        for row in group:
            time = context.configuration.effective_time
            if time:
                time_column = next(column for name, column, _ in columns(context) if name == time.column)
                if row[time_column] != row['observed_time']:
                    raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            if (any(row[n] != first[n] for n in metadata)
                    or values(row, context.configuration.business_keys) != values(first, context.configuration.business_keys)):
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            versions.append(Version(member, row['version_sk'], row['valid_from'], row['valid_to'], row['is_current'],
                values(row, context.configuration.tracked_attributes), values(row, context.configuration.nonhistorical_attributes), row['tracked_hash']))
        state = MemberState(context, values(first, context.configuration.business_keys), member, tuple(versions),
            first['observed_time'], AcceptedOrder(UUID(first['order_domain']), first['order_position']) if first['order_domain'] else None,
            UUID(first['last_event_id']) if first['last_event_id'] else None, first['last_event_digest'])
        validate_timeline(state)
        result.append(state)
    return tuple(result)


def schema_hash(context):
    return digest([schema(context), columns(context)])
