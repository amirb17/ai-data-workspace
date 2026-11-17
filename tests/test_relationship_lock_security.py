"""The public relationship API must lock via a capability, not direct UPDATE grants."""
from pathlib import Path

from app.services import relationship_service

PREPARE = Path("migrations/0100_relationship_lock_prepare.sql")
REVOKE = Path("migrations/0101_relationship_lock_revoke.sql")


def test_relationship_service_uses_scoped_lock_capability():
    import inspect

    source = inspect.getsource(relationship_service.gather)
    assert "public.lock_relationship_sources(%s,%s)" in source
    assert "(workspace, user['owner_key'])" in source
    assert "FOR UPDATE" not in source.split("# A reviewed")[1]


def test_lock_function_is_narrow_security_definer():
    sql = PREPARE.read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, pg_temp" in sql
    assert "w.owner = p_owner_key" in sql
    assert "FOR UPDATE" in sql
    assert "ORDER BY d.dataset_id" in sql
    assert "FROM PUBLIC" in sql
    assert "TO datarise_api" in sql
    assert "GRANT UPDATE (workspace_name)" not in sql
    assert "GRANT UPDATE (dataset_name)" not in sql


def test_revocation_is_separate_and_asserts_effective_privileges():
    sql = REVOKE.read_text(encoding="utf-8")
    assert "REVOKE UPDATE (workspace_name)" in sql
    assert "REVOKE UPDATE (dataset_name)" in sql
    assert "has_column_privilege" in sql
    assert "has_function_privilege" in sql
    assert "COMMIT;" in sql
