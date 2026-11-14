"""Gold worker may read dependency epochs but must not own epoch row locks.

The authority SQL performs the locking recheck against pinned input.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_worker_contract_does_not_take_epoch_share_lock():
    source = (ROOT / "app/db/processing_integration_repository.py").read_text(encoding="utf-8")
    assert "SELECT revision FROM processing_dependency_epochs WHERE dataset_id=%s'" in source
    assert "SELECT revision FROM processing_dependency_epochs WHERE dataset_id=%s FOR SHARE" not in source


def test_trusted_sql_pins_and_rechecks_epoch_under_lock():
    migration = (ROOT / "migrations/0043_processing_integration.sql").read_text(encoding="utf-8")
    assert "SELECT revision INTO epoch FROM processing_dependency_epochs WHERE dataset_id=x.dataset_id FOR SHARE;" in migration
    assert "IF epoch IS DISTINCT FROM (c->>'dependency_epoch')::bigint" in migration


def test_worker_runtime_does_not_permit_epoch_write_privilege():
    runtime = (ROOT / "app/processing_worker/runtime_security.py").read_text(encoding="utf-8")
    assert "'processing_dependency_epochs'" in runtime
    assert "has_any_column_privilege(current_user,n,'INSERT,UPDATE')" in runtime
