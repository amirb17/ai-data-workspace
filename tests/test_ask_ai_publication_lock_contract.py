"""Ask AI must not use privileged row locks through the public API role."""
from pathlib import Path
import inspect
from app.services import workspace_question_service as questions


def test_answer_publication_uses_owner_scoped_database_lock():
    source = inspect.getsource(questions.ask)
    assert "SELECT public.ask_ai_lock_owned_workspace(%s,%s)" in source
    assert "(workspace, user['owner_key'])" in source
    assert "metric_service.gather(conn, workspace, user, False)" in source
    assert "metric_service.gather(conn, workspace, user, True)" not in source


def test_migration_limits_grant_to_lock_execution():
    migration = Path("migrations/0105_ask_ai_owned_publication_lock.sql").read_text()
    assert "SECURITY DEFINER" in migration
    assert "SET search_path = pg_catalog, public" in migration
    assert "owner = p_owner_key" in migration
    assert "FOR UPDATE;" in migration
    assert "REVOKE ALL ON FUNCTION" in migration
    assert "GRANT EXECUTE ON FUNCTION" in migration
    assert "GRANT UPDATE" not in migration
