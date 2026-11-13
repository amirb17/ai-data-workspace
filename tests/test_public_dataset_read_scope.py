"""Public GET authorization must not grant UPDATE to the restricted DB role."""
from contextlib import contextmanager
from unittest.mock import patch
import pytest
from app.services import product_preview_service as svc
from app.security import public_launch


class Connection:
    def __init__(self, found=(None,)):
        self.found = found
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        return self

    def fetchone(self):
        return self.found


@contextmanager
def txn(connection):
    yield connection


USER = {"user_id": 123, "owner_key": "example:owner"}


def test_read_guard_does_not_require_update_privilege():
    connection = Connection()
    with patch.object(svc.database, "repository_transaction", lambda: txn(connection)):
        svc.authorize_read(10, 20, USER)
    sql, params = connection.queries[-1]
    assert "FOR SHARE" not in sql
    assert "account_status='ACTIVE'" in sql
    assert "w.owner=u.owner_key AND d.owner=u.owner_key" in sql
    assert params == (10, 20, 123, "example:owner")


def test_preview_guard_keeps_row_lock():
    connection = Connection()
    with patch.object(svc.database, "repository_transaction", lambda: txn(connection)):
        svc.authorize(10, 20, USER)
    assert "FOR SHARE OF u,w,d" in connection.queries[-1][0]


def test_guard_rejects_unowned_dataset():
    connection = Connection(found=None)
    with patch.object(svc.database, "repository_transaction", lambda: txn(connection)):
        with pytest.raises(PermissionError):
            svc.authorize_read(10, 20, USER)


def test_launch_gate_uses_read_guard_only_for_get():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "app/security/public_launch.py").read_text()
    assert "if request.method == 'GET':" in source
    assert "authorize_read(w, d, user)" in source
    assert "authorize(w, d, user)" in source
