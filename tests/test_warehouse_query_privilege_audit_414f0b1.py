"""414F0-B1 read-only audit contract. No PostgreSQL roles or cloud calls."""
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deployment" / "operations" / "warehouse_query_privilege_audit_414f0b1.sql"


def test_audit_is_read_only_and_reviews_all_sensitive_tables():
    sql = SCRIPT.read_text(encoding="utf-8")
    import re
    stripped = re.sub(r"--[^\n]*", "", sql)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
    # SQL privilege names in string literals are not executable commands.
    stripped = re.sub(r"'(?:''|[^'])*'", "''", stripped)
    assert not re.search(
        r"\b(CREATE|ALTER|DROP|GRANT|REVOKE|INSERT\s+INTO|UPDATE\s+"
        r"[a-z_]|DELETE\s+FROM|TRUNCATE|CALL|DO|COPY|SET|RESET)\b",
        stripped, flags=re.I,
    )
    assert stripped.count(";") == 4
    for table in (
        "warehouse_query_dispatch_checkpoints",
        "warehouse_query_dispatch_revocations",
        "warehouse_query_send_intents",
        "warehouse_query_send_journal",
        "warehouse_query_recovery_attestations",
        "warehouse_query_provider_policies",
        "warehouse_query_attempt_publication_pins",
        "warehouse_query_attempt_reservation_links",
        "warehouse_query_publication_retirement_fences",
    ):
        assert table in stripped
    for role in ("datarise_authority_owner", "datarise_api", "datarise_worker",
                 "datarise_verifier", "datarise_dispatch"):
        assert role in stripped


def test_audit_inspects_membership_and_column_grants():
    sql = SCRIPT.read_text(encoding="utf-8")
    assert "pg_has_role" in sql
    assert "has_table_privilege" in sql
    assert "has_column_privilege" in sql
    assert "current_schema()" in sql
