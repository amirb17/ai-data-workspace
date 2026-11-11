"""414F0-B6 guards the diagnostic-only function/trigger inventory."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deployment/operations/warehouse_query_function_trigger_audit_414f0b6.sql"


def clean(sql):
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def test_catalog_inventory_is_read_only():
    raw = SCRIPT.read_text(encoding="utf-8")
    cleaned = clean(raw)
    assert cleaned.count(";") == 3
    assert not re.search(r"\b(CREATE|DROP|ALTER|GRANT|REVOKE|INSERT|UPDATE|DELETE|TRUNCATE|DO|CALL|COPY|SET|RESET)\b",
                         cleaned, flags=re.I)
    assert "pg_catalog.pg_proc" in raw
    assert "pg_catalog.pg_trigger" in raw
    assert "has_function_privilege" in raw
    assert "prosecdef" in raw
    assert "proconfig" in raw
    assert "current_schema()" in raw


def test_all_critical_function_and_trigger_names_included():
    raw = SCRIPT.read_text(encoding="utf-8")
    names = [
        "warehouse_query_dispatch_record_guard",
        "warehouse_query_dispatch_revoke_notice",
        "warehouse_query_send_intent_guard",
        "warehouse_query_send_journal_guard",
        "warehouse_query_recovery_attestation_guard",
        "warehouse_query_provider_policy_guard",
        "warehouse_query_retirement_fence_guard",
        "warehouse_query_publication_dependency_present",
        "warehouse_query_dispatch_checkpoint_guard",
        "warehouse_send_journal_guard",
        "warehouse_provider_policy_guard",
    ]
    assert all(f"('{name}')" in raw for name in names)


def test_missing_schema_is_not_misrepresented_as_success():
    raw = SCRIPT.read_text(encoding="utf-8")
    assert "LEFT JOIN actual" in raw
    assert "IS NOT NULL AS exists" in raw
    assert "public_execute" in raw
