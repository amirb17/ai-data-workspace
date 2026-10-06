"""Version 1 deterministic identity over explicitly selected business columns.

No pandas dependency, process-local hash(), inferred keys or merge execution.
Values are already typed/cleaned by the pinned Silver policy.
"""
import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal

NORMALIZATION_VERSION = 1
OPERATIONAL_PREFIXES = ("_dq_", "_source_", "_bronze_", "_gold_", "_application_", "_datarise_")


def business_column(name):
    return isinstance(name, str) and bool(name.strip()) and not name.lower().startswith(OPERATIONAL_PREFIXES)


def normalize_value(value):
    if value is None or isinstance(value, float) and math.isnan(value):
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("Non-finite numbers cannot define row identity")
        # No Decimal.normalize(): it rounds under the ambient decimal context.
        text = format(number, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return ["number", "0" if number == 0 else text]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp identity requires an explicit timezone")
        return ["timestamp", value.astimezone(timezone.utc).isoformat(timespec="microseconds")]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, str):
        return ["string", value]  # Exact, case-sensitive; no implicit trim/coercion.
    raise ValueError("Unsupported row identity value type")


def _hash(row, columns, key):
    if not columns or len(set(columns)) != len(columns) or any(not business_column(c) for c in columns):
        raise ValueError("Explicit unique business columns are required")
    values = []
    for column in columns:
        if column not in row:
            raise ValueError("Identity column is missing")
        normalized = normalize_value(row[column])
        if key and normalized == ["null"]:
            raise ValueError("Business key values cannot be null")
        values.append([column, normalized])
    payload = json.dumps([NORMALIZATION_VERSION, "key" if key else "content", values], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def business_key_hash(row, ordered_columns):
    return _hash(row, ordered_columns, True)


def row_content_hash(row, business_columns):
    return _hash(row, sorted(business_columns), False)
