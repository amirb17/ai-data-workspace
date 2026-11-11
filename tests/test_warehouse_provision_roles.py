"""Offline contract tests for the warehouse identity-only provisioning workflow."""
from pathlib import Path

from tools import warehouse_provision_roles as tool


class FakeConn:
    def __init__(self, existing=None, *, role="postgres", superuser=True):
        self.existing = existing or {}
        self.role = role
        self.superuser = superuser

    def execute(self, query, params=()):
        if "current_database(), session_user" in query:
            return FakeResult(("ai_data_workspace", self.role, self.superuser))
        if "FROM pg_namespace WHERE nspname" in query:
            return FakeResult((1,))
        if "FROM pg_roles r WHERE rolname" in query:
            return FakeResult(self.existing.get(params[0]))
        raise AssertionError("Unexpected query")


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def test_missing_roles_are_detected_without_role_ddl():
    missing = tool.audit(FakeConn())
    assert len(missing) == 8
    assert "MISSING:datarise_wh_owner" in missing
    assert "MISSING:datarise_wh_control" in missing


def test_existing_safe_login_roles_are_recognized():
    current = {}
    for role, login in tool.EXPECTED.items():
        current[role] = (login, False, False, False, False, False, False, 0)
    assert tool.audit(FakeConn(current)) == []


def test_membership_or_privilege_escalation_fails_audit():
    current = {"datarise_wh_control": (
        True, False, False, False, False, False, False, 1
    )}
    assert "UNSAFE:datarise_wh_control" in tool.audit(FakeConn(current))


def test_non_superuser_admin_context_rejected():
    import pytest
    with pytest.raises(RuntimeError, match="REVIEWED_LOCAL_ADMIN_CONTEXT"):
        tool.audit(FakeConn(role="app_user", superuser=False))


def test_role_script_creates_no_warehouse_table_or_cloud_privileges():
    ddl = tool.DDL.read_text(encoding="utf-8-sig").upper()
    assert "CREATE ROLE %I" in ddl
    assert "PASSWORD NULL" in ddl
    assert "NOSUPERUSER" in ddl
    assert "NOINHERIT" in ddl
    assert "GRANT " not in ddl
    assert "SET ROLE " not in ddl
