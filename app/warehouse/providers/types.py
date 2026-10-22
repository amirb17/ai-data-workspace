"""Exact scalar mapping. Wider logical Decimal/integer values fail closed."""
from datetime import date, datetime, timezone
from decimal import Decimal

from app.warehouse.keys import canonical_value
from app.warehouse.providers.base import ErrorCode, ProviderError

TYPES = {'STRING': 'STRING', 'INTEGER': 'INT64', 'BOOLEAN': 'BOOL',
         'DECIMAL': 'BIGNUMERIC', 'DATE': 'DATE', 'TIMESTAMP': 'TIMESTAMP'}


def decimal_type(value):
    if type(value) is not Decimal or not value.is_finite():
        raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
    # canonical_value removes insignificant fractional zeros without ambient
    # Decimal-context rounding. BIGNUMERIC's partial 77th digit is not used.
    text = canonical_value(value)[1].lstrip('-')
    whole, _, fraction = text.partition('.')
    integer_digits = len(whole.lstrip('0'))
    scale = len(fraction)
    if integer_digits <= 29 and scale <= 9:
        return 'NUMERIC'
    if integer_digits <= 38 and scale <= 38 and integer_digits + scale <= 76:
        return 'BIGNUMERIC'
    raise ProviderError(ErrorCode.SCHEMA_MISMATCH)


def encode(value, kind):
    if value is None:
        return None
    expected = {'STRING': str, 'INT64': int, 'BOOL': bool, 'DATE': date,
                'TIMESTAMP': datetime, 'NUMERIC': Decimal, 'BIGNUMERIC': Decimal}
    if kind not in expected or type(value) is not expected[kind]:
        raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
    canonical_value(value)
    if kind == 'INT64':
        if not -(1 << 63) <= value < 1 << 63:
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        return str(value)  # JSON numbers must not pass through binary floats.
    if kind in ('NUMERIC', 'BIGNUMERIC'):
        found = decimal_type(value)
        if kind == 'NUMERIC' and found != kind:
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        return canonical_value(value)[1]
    if kind == 'TIMESTAMP':
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds')
    if kind == 'DATE':
        return value.isoformat()
    return value


def decode(value, kind):
    if value is None:
        return None
    if kind in ('NUMERIC', 'BIGNUMERIC'):
        if type(value) not in (str, Decimal):
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        value = Decimal(value)
    elif kind == 'INT64':
        if type(value) not in (str, int):
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        value = int(value)
    elif kind == 'DATE' and type(value) is str:
        value = date.fromisoformat(value)
    elif kind == 'TIMESTAMP' and type(value) is str:
        value = datetime.fromisoformat(value)
    encode(value, kind)
    return value


def scalar(tag):
    if tag == ['null']:
        return None
    kinds = {'string': 'STRING', 'integer': 'INT64', 'boolean': 'BOOL',
             'decimal': 'BIGNUMERIC', 'date': 'DATE', 'timestamp': 'TIMESTAMP'}
    if not isinstance(tag, (tuple, list)) or len(tag) != 2 or tag[0] not in kinds:
        raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
    result = decode(tag[1], kinds[tag[0]])
    if canonical_value(result) != list(tag):
        raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
    return result
