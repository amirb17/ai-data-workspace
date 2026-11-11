"""Offline security tests for local one-duty warehouse credential bootstrap."""
import pytest
from psycopg.conninfo import conninfo_to_dict

from tools.warehouse_local_role_credentials import (
    _approved_admin, _check_role, _read_password, _role_dsn, bootstrap,
)


class _Result:
    def __init__(self, value):
        self.value=value
    def fetchone(self):
        return self.value


class _Fake:
    def __init__(self, context=None, attributes=None):
        self.context=context or ("ai_data_workspace","postgres","postgres",True)
        self.attributes=attributes or (True,False,False,False,False,False,False,0)
    def execute(self, query, params=()):
        if "current_database(),session_user,current_user" in query:
            return _Result(self.context)
        if "rolcanlogin,rolsuper" in query:
            return _Result(self.attributes)
        raise AssertionError("unexpected query")


def test_admin_requires_verified_database_and_superuser():
    settings={"host":"127.0.0.1","user":"postgres","dbname":"ai_data_workspace"}
    _approved_admin(_Fake(),settings)
    with pytest.raises(RuntimeError,match="LOOPBACK"):
        _approved_admin(_Fake(),dict(settings,host="remote.example"))
    with pytest.raises(RuntimeError,match="LOOPBACK"):
        _approved_admin(_Fake(),dict(settings,hostaddr="192.0.2.3"))
    with pytest.raises(RuntimeError,match="CONTEXT_REQUIRED"):
        _approved_admin(_Fake(context=("ai_data_workspace","postgres","postgres",False)),settings)


def test_role_must_be_direct_nonprivileged_login():
    _check_role(_Fake(),"datarise_wh_submit")
    with pytest.raises(RuntimeError,match="ATTRIBUTES_DRIFT"):
        _check_role(_Fake(attributes=(True,True,False,False,False,False,False,0)),
                    "datarise_wh_submit")
    with pytest.raises(RuntimeError,match="ATTRIBUTES_DRIFT"):
        _check_role(_Fake(attributes=(True,False,False,False,False,False,False,1)),
                    "datarise_wh_submit")


def test_worker_dsn_uses_only_explicit_role_and_connection_parameters():
    settings={
        "host":"localhost", "port":"5432","dbname":"ai_data_workspace",
        "user":"postgres","password":"administrator-secret",
        "application_name":"admin-session",
    }
    uri=_role_dsn(settings,"datarise_wh_submit","private-worker-secret")
    worker=conninfo_to_dict(uri)
    assert worker["user"]=="datarise_wh_submit"
    assert worker["dbname"]=="ai_data_workspace"
    assert worker["password"]=="private-worker-secret"
    assert worker["password"]!=settings["password"]
    assert "application_name" not in worker


def test_password_confirmation_and_minimum_length(monkeypatch):
    values=iter(["A"*26, "A"*26])
    monkeypatch.setattr("tools.warehouse_local_role_credentials.getpass",
                        lambda prompt: next(values))
    assert _read_password("datarise_wh_submit")=="A"*26
    values=iter(["A"*26, "B"*26])
    monkeypatch.setattr("tools.warehouse_local_role_credentials.getpass",
                        lambda prompt: next(values))
    with pytest.raises(RuntimeError,match="PASSWORD_CONFIRMATION"):
        _read_password("datarise_wh_submit")


def test_reject_unknown_duty_without_connecting_to_db():
    with pytest.raises(ValueError,match="UNSUPPORTED_QUERY_RUNTIME_DUTY"):
        bootstrap("unknown")


def test_missing_apply_only_accepts_known_duty():
    from tools.warehouse_runtime_role_acceptance import DUTIES
    assert len(DUTIES)==5
    assert "query_control" in DUTIES
