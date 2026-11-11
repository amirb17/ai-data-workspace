"""Offline tests for REAL HTTP acceptance runner; no cloud or database IO."""
from types import SimpleNamespace

import pytest

from tools.warehouse_live_app_e2e import AcceptanceError, run


class Reply:
    headers = {"content-type": "application/json"}
    def __init__(self, status, data):
        self.status_code = status
        self.data = data
    def json(self):
        return self.data


class FakeSession:
    def __init__(self, *, failure=None):
        self.headers = {}
        self.cookies = SimpleNamespace(set=lambda *args, **kwargs: None)
        self.history = []
        self.failure = failure
        self.handle = "a" * 32
    def post(self, url, *, json, timeout, allow_redirects):
        assert allow_redirects is False
        self.history.append(("POST", url, json))
        if url.endswith("/validate"):
            return Reply(503, {}) if self.failure == "disabled" else Reply(
                200, {"budget_decision": "ALLOWED"}
            )
        if url.endswith("/execute"):
            return Reply(200, {"attempt_handle": self.handle, "status": "SUCCEEDED"})
        if url.endswith("/refresh"):
            raise AssertionError("Finished job must not refresh")
        return Reply(201, {
            "attempt_handle": self.handle,
            "reused": len([x for x in self.history if x[1].endswith("/queries")]) > 1,
        })
    def get(self, url, *, timeout, allow_redirects):
        assert allow_redirects is False
        self.history.append(("GET", url, None))
        if url.endswith("/results"):
            return Reply(200, {"columns": ["check_value"], "rows": [[1]]})
        raise AssertionError("Terminal job must not poll")


def args():
    return SimpleNamespace(
        backend="http://127.0.0.1:8000",
        origin="http://localhost:5173",
        workspace=18,
        model=42,
        sql="SELECT check_value FROM model_42 LIMIT 1",
        timeout=90,
    )


def test_approved_end_to_end_state_verifies_exact_admission_and_results(capsys):
    session = FakeSession()
    assert run(args(), session=session, cookie="session", csrf="csrf",
               wait=lambda _: None) == 0
    assert len(session.history) == 5
    assert [method for method, _, _ in session.history] == [
        "POST", "POST", "POST", "POST", "GET"
    ]
    assert session.history[1][2] == session.history[2][2]
    assert "LIVE VERIFIED" in capsys.readouterr().out


def test_actual_disabled_http_gate_is_not_reported_as_success():
    with pytest.raises(AcceptanceError, match="WAREHOUSE_API_STATUS_503"):
        run(args(), session=FakeSession(failure="disabled"),
            cookie="session", csrf="csrf", wait=lambda _: None)


@pytest.mark.parametrize("sql", [
    "DELETE FROM model_42",
    "SELECT 1; DROP TABLE users",
    "SELECT 1 --comment",
])
def test_unsafe_sql_is_rejected_without_request(sql):
    value = args()
    value.sql = sql
    session = FakeSession()
    with pytest.raises(AcceptanceError):
        run(value, session=session, cookie="session", csrf="csrf")
    assert session.history == []


def test_remote_hostname_never_receives_credentials():
    value = args()
    value.backend = "https://external.example.org"
    session = FakeSession()
    with pytest.raises(AcceptanceError, match="EXPLICIT_LOOPBACK"):
        run(value, session=session, cookie="session", csrf="csrf")
    assert session.history == []
