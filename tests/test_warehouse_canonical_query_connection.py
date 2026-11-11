"""Offline fail-closed canonical query-control boundary acceptance."""
import pytest

from app.db.warehouse_canonical_query_connection import (
    REQUIRED_ISOLATED, REQUIRED_PUBLIC_READS, SEARCH_PATH, verify_scope,
)


class _One:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value


class _Connection:
    def __init__(self, *, create=False, public_create=False, shadow=False,
                 missing_read=None, missing_isolated=None, user="datarise_wh_control"):
        self.create = create
        self.public_create = public_create
        self.shadow = shadow
        self.missing_read = missing_read
        self.missing_isolated = missing_isolated
        self.user = user
        self.reads = []

    def execute(self, query, params=()):
        if "has_schema_privilege(current_user,'public','USAGE')" in query:
            return _One((self.user, self.user, "ai_data_workspace",
                         True, self.create, self.public_create))
        if "c.relname LIKE 'warehouse_query_%'" in query:
            return _One((1,) if self.shadow else None)
        if "has_table_privilege" in query:
            self.reads.append(params[0])
            return _One((params[0] != self.missing_read,))
        if "to_regclass" in query:
            return _One((params[0] != self.missing_isolated,))
        raise AssertionError("Unexpected SQL")


def test_dual_schema_scope_checks_all_required_canonical_reads():
    conn = _Connection()
    verify_scope(conn)
    assert len(conn.reads) == len(REQUIRED_PUBLIC_READS)
    assert all(table.startswith("public.") for table in conn.reads)
    assert len(REQUIRED_ISOLATED) >= 4
    assert SEARCH_PATH.startswith("pg_catalog, public,")


@pytest.mark.parametrize("kwargs", [
    {"create": True},
    {"public_create": True},
    {"shadow": True},
    {"user": "postgres"},
    {"missing_read": "public.users"},
    {"missing_isolated": "datarise_warehouse_dev.warehouse_query_attempt_registry"},
])
def test_dual_schema_scope_rejects_unsafe_context(kwargs):
    with pytest.raises(RuntimeError):
        verify_scope(_Connection(**kwargs))
