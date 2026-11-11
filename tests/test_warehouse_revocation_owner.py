"""Offline safety coverage for atomic cross-schema revocation trigger ownership.

The real PostgreSQL rehearsal and real login checks are separate. These tests
assert that database ownership is never delegated to a runtime principal and
that only the precise trigger read/insert capabilities are issued.
"""
import pytest

from tools.warehouse_revocation_owner import (
    install_revocation_owner, OWNER, READS, REVOCATION_FUNCTION
)


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, *, role_safe=True, owner_safe=True):
        self.calls = []
        self.role_safe = role_safe
        self.owner_safe = owner_safe

    def execute(self, query, params=()):
        stmt = query.as_string(self) if hasattr(query, "as_string") else query
        self.calls.append((stmt, params))
        if "FROM pg_roles r WHERE rolname" in stmt:
            if self.role_safe:
                return _Result((False, False, False, False,
                                False, False, False, 0))
            return _Result((True, False, False, False,
                            False, False, False, 0))
        if "FROM pg_proc p JOIN pg_namespace" in stmt:
            return _Result((
                True, OWNER, False,
                ["search_path=pg_catalog, datarise_warehouse_dev, pg_temp"]
            ) if self.owner_safe else (False, "postgres", True, None))
        return _Result(None)


def test_nonlogin_owner_is_required_before_any_ddl():
    conn = _Conn(role_safe=False)
    with pytest.raises(RuntimeError, match="ROLE_NOT_RESTRICTED"):
        install_revocation_owner(conn)
    assert len(conn.calls) == 1


def test_owner_receives_only_specific_trigger_capabilities():
    # psycopg Identifier SQL compilation requires connection encoding and
    # escapes; use psycopg's explicit SQL composition as the audit boundary.
    from unittest.mock import patch
    conn = _Conn()
    class _Composed:
        def __init__(self, template):
            self.template = template
        def format(self, *parts):
            pieces = [part.string if hasattr(part, "string")
                      else part.body if hasattr(part, "body") else str(part)
                      for part in parts]
            tokens = self.template.split("{}")
            return _Statement("".join(
                value + (pieces[index] if index < len(pieces) else "")
                for index, value in enumerate(tokens)))
    class _Statement:
        def __init__(self, body):
            self.body = body
        def as_string(self, context):
            return self.body
    with patch("tools.warehouse_revocation_owner.sql.SQL", _Composed):
        with patch("tools.warehouse_revocation_owner.sql.Identifier",
                   side_effect=lambda value: type("Identifier", (), {"string": value})()):
            install_revocation_owner(conn)
    statements = "\n".join(s for s, _ in conn.calls[1:])
    assert "ALTER FUNCTION datarise_warehouse_dev." + REVOCATION_FUNCTION + "() OWNER TO " + OWNER in statements
    assert "SECURITY DEFINER" in statements
    assert "REVOKE CREATE ON SCHEMA datarise_warehouse_dev FROM " + OWNER in statements
    assert "GRANT SELECT ON datarise_warehouse_dev.warehouse_query_dispatch_checkpoints" in statements
    assert statements.count("GRANT SELECT ON") == len(READS) + 1
    assert "GRANT INSERT ON datarise_warehouse_dev.warehouse_query_dispatch_revocations" in statements
    assert "GRANT ALL ON" not in statements
    assert "GRANT UPDATE ON" not in statements
    assert "GRANT DELETE ON" not in statements
    assert "datarise_wh_control" not in statements
    assert "postgres SECURITY DEFINER" not in statements


def test_failed_owner_postcondition_does_not_claim_success():
    conn = _Conn(owner_safe=False)
    from unittest.mock import patch
    class _Dummy:
        def __init__(self, value): self.value = value
        def format(self, *parts): return self
        def as_string(self, context): return self.value
    with patch("tools.warehouse_revocation_owner.sql.SQL", side_effect=_Dummy):
        with patch("tools.warehouse_revocation_owner.sql.Identifier", side_effect=_Dummy):
            with pytest.raises(RuntimeError, match="HARDENING_FAILED"):
                install_revocation_owner(conn)
