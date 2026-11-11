"""Offline invariants for the rollback-only atomic cutover rehearsal."""
import pytest

from tools.warehouse_atomic_cutover import (
    _function_source_rewrite,
    _rebind_trigger_target,
    _replace_canonical_identifiers,
)


def test_rewrite_preserves_auth_literals_and_public_boundaries():
    function = """
    CREATE OR REPLACE FUNCTION datarise_warehouse_dev.warehouse_query_guard()
    RETURNS trigger LANGUAGE plpgsql AS $function$
    BEGIN
      IF TG_TABLE_NAME='users' THEN
        PERFORM 1 FROM users WHERE owner_key=NEW.owner_key;
      END IF;
      PERFORM 1 FROM warehouse_query_attempt_registry WHERE attempt_id=1;
      PERFORM 1 FROM public.workspaces WHERE workspace_id=NEW.workspace_id;
      -- SELECT * FROM workspaces;
      RETURN NEW;
    END
    $function$;
    """
    changed, seen = _function_source_rewrite(function)
    assert "FROM public.users WHERE" in changed
    assert "TG_TABLE_NAME='users'" in changed
    assert "-- SELECT * FROM workspaces;" in changed
    assert "FROM public.workspaces" in changed
    assert "public.public" not in changed
    assert "users" in seen
    assert "workspaces" not in seen
    assert "warehouse_query_attempt_registry" in changed


@pytest.mark.parametrize("source", [
    "CREATE TRIGGER warehouse_dispatch_session_revoke BEFORE UPDATE "
    "ON datarise_warehouse_dev.auth_sessions FOR EACH ROW "
    "EXECUTE FUNCTION datarise_warehouse_dev.warehouse_query_dispatch_revoke_notice()",
    'CREATE TRIGGER warehouse_dispatch_session_revoke BEFORE UPDATE '
    'ON "datarise_warehouse_dev"."auth_sessions" FOR EACH ROW '
    'EXECUTE FUNCTION datarise_warehouse_dev.warehouse_query_dispatch_revoke_notice()',
])
def test_trigger_target_rebind_without_touching_function(source):
    rewritten = _rebind_trigger_target(source, "auth_sessions")
    assert 'ON "public"."auth_sessions"' in rewritten
    assert "datarise_warehouse_dev.warehouse_query_dispatch_revoke_notice()" in rewritten


def test_unknown_or_already_public_trigger_is_rejected():
    with pytest.raises(RuntimeError, match="OUTSIDE_CANONICAL"):
        _rebind_trigger_target("ON datarise_warehouse_dev.not_allowed ", "not_allowed")
    with pytest.raises(RuntimeError, match="SCHEMA_DRIFT"):
        _rebind_trigger_target("CREATE TRIGGER a ON public.users FOR EACH ROW", "users")


def test_unbalanced_function_body_is_rejected():
    with pytest.raises(RuntimeError, match="UNBALANCED"):
        _replace_canonical_identifiers("SELECT * FROM users WHERE owner='unterminated")
