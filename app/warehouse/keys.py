"""Frozen surrogate-key wire format V1. Pure functions; no storage or execution."""
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from uuid import UUID

ALGORITHM_VERSION = 1
MAX_COMPONENT_BYTES = 65536


def canonical_value(value):
    """Type-tagged scalar encoding; strings preserve Unicode code points exactly."""
    if value is None:
        return ['null']
    if isinstance(value, bool):
        return ['boolean', value]
    if isinstance(value, int):
        if value.bit_length()>13600:
            raise ValueError('Integer identity exceeds supported precision')
        return ['integer', str(value)]
    if isinstance(value, Decimal):
        if not value.is_finite() or len(value.as_tuple().digits)>4096 or abs(value.as_tuple().exponent)>4096:
            raise ValueError('Unsupported decimal identity')
        text = format(value, 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
        return ['decimal', '0' if value == 0 else text]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Identity timestamps require an explicit offset')
        return ['timestamp', value.astimezone(timezone.utc).isoformat(timespec='microseconds')]
    if isinstance(value, date):
        return ['date', value.isoformat()]
    if isinstance(value, str):
        try:
            encoded = value.encode('utf-8', errors='strict')
        except UnicodeError as error:
            raise ValueError('Invalid Unicode identity') from error
        if len(encoded)>MAX_COMPONENT_BYTES:
            raise ValueError('Identity component exceeds byte bound')
        return ['string', value]
    raise ValueError('Unsupported identity scalar; floats and nested values are rejected')


def canonical_key(namespace: UUID | str, role: str, columns: Sequence[str], row: Mapping,
                  *, valid_from: date | datetime | None = None, algorithm_version: int = 1) -> bytes:
    if type(algorithm_version) is not int or algorithm_version != ALGORITHM_VERSION:
        raise ValueError('Unsupported key algorithm version')
    if role not in ('FACT', 'DIMENSION_MEMBER', 'DIMENSION_VERSION'):
        raise ValueError('Unknown warehouse key role')
    if (not isinstance(row, Mapping) or not isinstance(columns, (tuple, list)) or not columns or len(columns)>32
            or not all(isinstance(c,str) for c in columns) or len(set(columns)) != len(columns)):
        raise ValueError('Explicit ordered distinct identity columns are required')
    identity = []
    for column in columns:
        if (not column or len(column)>255 or column != column.strip() or any(ord(c)<32 for c in column) or column not in row):
            raise ValueError('Identity field is invalid or missing')
        value = canonical_value(row[column])
        if value == ['null']:
            raise ValueError('Null business/event key is rejected')
        identity.append([column, value])
    boundary = None
    if role == 'DIMENSION_VERSION':
        if not isinstance(valid_from, (date, datetime)):
            raise ValueError('SCD2 version key requires an explicit date/timestamp boundary')
        boundary = canonical_value(valid_from)
    elif valid_from is not None:
        raise ValueError('Only SCD2 version keys include valid_from')
    try:
        namespace_text = str(UUID(str(namespace)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError('Model key namespace must be a UUID') from error
    payload = ['datarise-warehouse-key', algorithm_version, namespace_text, role, identity, boundary]
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def surrogate_key(namespace: UUID | str, role: str, columns: Sequence[str], row: Mapping,
                  *, valid_from: date | datetime | None = None, algorithm_version: int = 1) -> str:
    return hashlib.sha256(canonical_key(namespace, role, columns, row, valid_from=valid_from,
                                        algorithm_version=algorithm_version)).hexdigest()


def model_surrogate_key(namespace, configuration, row: Mapping, *, algorithm_version: int = 1) -> str:
    """Use reviewed roles/time policy; attribute/ingestion changes do not define identity."""
    from app.schemas.warehouse_modeling import FactConfiguration, DimensionConfiguration
    from app.warehouse.time_policy import normalize_time
    if isinstance(configuration, FactConfiguration):
        return surrogate_key(namespace,'FACT',configuration.grain.identity_columns,row,algorithm_version=algorithm_version)
    if not isinstance(configuration, DimensionConfiguration):
        raise ValueError('Typed FACT/DIMENSION configuration required')
    if configuration.scd_type == 'SCD_TYPE_1':
        return surrogate_key(namespace,'DIMENSION_MEMBER',configuration.business_keys,row,algorithm_version=algorithm_version)
    policy = configuration.effective_time
    if policy.column not in row:
        raise ValueError('Effective-time value missing')
    effective = row[policy.column]
    if (policy.interpretation=='SOURCE_TIMEZONE' and isinstance(effective, datetime)
            and (effective.tzinfo is None or effective.utcoffset() is None)):
        # Named-zone rules can change with tzdata updates. Future loaders must
        # pin the resolved instant in source evidence before generating/replaying
        # historical keys; do not make key identity depend on ambient tzdata.
        raise ValueError('SOURCE_TIMEZONE version keys require a pinned offset-aware instant')
    return surrogate_key(namespace,'DIMENSION_VERSION',configuration.business_keys,row,
                         valid_from=normalize_time(effective,policy),algorithm_version=algorithm_version)
