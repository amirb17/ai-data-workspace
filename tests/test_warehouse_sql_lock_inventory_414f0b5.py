"""414F0-B5: actual warehouse source contains additional locking requirements."""
from pathlib import Path

from app.security.warehouse_sql_lock_inventory import (
    SOURCES, inspect_sql_locks, lock_review_status,
)

ROOT = Path(__file__).resolve().parents[1]


def test_actual_repositories_have_recorded_lock_dependencies():
    findings = inspect_sql_locks(ROOT)
    assert findings
    assert all(item.review_status == "REQUIRES_EFFECTIVE_PRIVILEGE_REVIEW" for item in findings)
    # Confirm cross-module lock dependencies cannot be erased from the
    # earlier draft just by dropping the lock regex or omitting key modules.
    assert any(f.file.endswith("warehouse_query_send_repository.py") and f.mode == "UPDATE" for f in findings)
    assert any(f.file.endswith("warehouse_publication_cleanup_inspection_service.py") and f.mode == "SHARE" for f in findings)
    assert any(f.file.endswith("warehouse_sql_dispatch_checkpoint_service.py") and f.mode == "SHARE" for f in findings)


def test_findings_are_deterministic_and_never_authorize_grants():
    first = inspect_sql_locks(ROOT)
    assert first == inspect_sql_locks(ROOT)
    result = lock_review_status(ROOT)
    assert result["verdict"] == "NO_GO"
    assert not result["review_complete"]
    assert result["lock_count"] == len(first)
    assert "TRIGGER_AND_FUNCTION_PRIVILEGES_NOT_VALIDATED" in result["blockers"]


def test_fixed_scope_and_no_connection_or_execution_dependencies():
    import ast
    path = ROOT / "app/security/warehouse_sql_lock_inventory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert len(SOURCES) == 7
    assert all((ROOT / p).is_file() for p in SOURCES)
    imports = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any(name.startswith(("psycopg", "google", "boto3")) for name in imports)
