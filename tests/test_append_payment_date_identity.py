"""Calendar/timezone behavior at APPEND boundary for payment deliveries.

A naive datetime must never be silently assigned a timezone; a real calendar
date or an explicitly UTC-aware timestamp is a valid row identity.
"""
from datetime import date
import pandas as pd
from app.processing.append_engine import append_rows


POLICY = {
    "load_strategy": "APPEND",
    "normalization_version": 1,
    "schema_columns": [
        {"name": "payment_id"}, {"name": "employee_id"},
        {"name": "payment_date"}, {"name": "payment_amount"},
    ],
    "business_keys": [],
    "event_time_column": None,
}
APP = {
    "upload_request_id": 4, "application_id": 2,
    "policy_id": 2, "applied_rule_version": 1,
}
COLS = [x["name"] for x in POLICY["schema_columns"]]


def _apply(value):
    incoming = pd.DataFrame([{
        "payment_id": "PAY-1001", "employee_id": 2206113781,
        "payment_date": value, "payment_amount": 55000,
    }])
    return append_rows(pd.DataFrame(columns=COLS), incoming, POLICY, APP)


def test_calendar_date_identity_is_accepted():
    state, counts, reasons = _apply(date(2026, 8, 31))
    assert len(state) == 1 and counts["inserted_rows"] == 1
    assert reasons == []


def test_timezone_aware_payment_datetime_is_accepted():
    state, counts, reasons = _apply(pd.Timestamp("2026-08-31T00:00:00Z"))
    assert len(state) == 1 and counts["inserted_rows"] == 1
    assert reasons == []


def test_naive_datetime_is_rejected_without_timezone_inference():
    state, counts, reasons = _apply(pd.Timestamp("2026-08-31T00:00:00"))
    assert state.empty and counts["incremental_rejected_rows"] == 1
    assert reasons[0]["reason"] == "INVALID_IDENTITY_OR_EVENT_TIME"
