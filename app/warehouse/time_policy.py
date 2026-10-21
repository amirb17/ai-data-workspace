"""Explicit time interpretation only; no historical inference or row mutation."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from app.schemas.warehouse_modeling import TimePolicy


def normalize_time(value: date | datetime, policy: TimePolicy) -> date | datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(timezone.utc)
        zone = timezone.utc if policy.interpretation == 'UTC' else ZoneInfo(policy.timezone)
        candidates = set()
        for fold in (0, 1):
            candidate = value.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
            if candidate.astimezone(zone).replace(tzinfo=None) == value:
                candidates.add(candidate)
        if len(candidates) != 1:
            raise ValueError('Ambiguous or nonexistent local business time; supply an explicit offset')
        return candidates.pop()
    if isinstance(value, date):
        return value  # DATE is a separate domain, not an invented midnight instant.
    raise ValueError('Business/effective time must be a typed date or timestamp')
