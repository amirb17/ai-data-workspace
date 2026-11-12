"""Offline tests for secure interactive worker launch and live health probe."""
import base64
from unittest.mock import patch

import pytest

from app.warehouse.worker_rpc_protocol import WorkerRpcRejected
from tools.warehouse_local_worker_run import PORTS, local_dsn, startup
from tools.warehouse_local_worker_probe import probe


def test_worker_database_password_is_not_command_argument_or_logged():
    dsn=local_dsn("accounting","secret-local-only")
    assert "datarise_wh_account" in dsn
    assert "secret-local-only" in dsn  # only in process memory
    with pytest.raises(ValueError,match="DUTY_NOT_ALLOWED"):
        local_dsn("query_control","secret")
    with pytest.raises(ValueError,match="PASSWORD_REQUIRED"):
        local_dsn("accounting","")


def test_known_ports_are_fixed_per_duty():
    assert PORTS=={
        "provider_submission":19081,
        "recovery_attestation":19082,
        "accounting":19083,
        "result_authorization":19084,
    }
    with pytest.raises(ValueError,match="DUTY_PORT"):
        startup("accounting",19081,password_reader=lambda prompt:"secret")


def test_startup_requires_login_acceptance_before_opening_listener():
    calls=[]
    def reject(duty,dsn):
        calls.append("accepted-called")
        raise RuntimeError("NO_VALID_LOGIN")
    with patch("tools.warehouse_local_worker_run.accept",reject):
        with patch("tools.warehouse_local_worker_run.create_server") as server:
            with pytest.raises(RuntimeError,match="NO_VALID_LOGIN"):
                startup("accounting",19083,password_reader=lambda prompt:"secret",
                        output=lambda item:calls.append(item),serve=False)
            server.assert_not_called()
    assert calls==["accepted-called"]


def test_startup_uses_a_single_role_and_does_not_print_password():
    output=[]
    secrets=[]
    class FakeServer:
        def server_close(self):
            output.append("server-closed")
    def fake_accept(duty,dsn):
        assert duty=="accounting"
        assert "user=datarise_wh_account" in dsn
    def fake_create_server(**kwargs):
        secrets.append(kwargs["secret"])
        assert kwargs["duty"]=="accounting"
        assert kwargs["port"]==19083
        assert callable(kwargs["boundary"])
        return FakeServer()
    with patch("tools.warehouse_local_worker_run.accept",fake_accept),patch(
            "tools.warehouse_local_worker_run.create_server",fake_create_server):
        startup("accounting",19083,password_reader=lambda prompt:"do-not-print",
                output=lambda item:output.append(item),serve=False)
    assert len(secrets)==1 and len(secrets[0])==32
    assert not any("do-not-print" in line for line in output)
    assert any("worker_started" in line for line in output)
    assert "server-closed" in output


def test_live_probe_requires_signed_identity_acknowledgement():
    with patch("tools.warehouse_local_worker_probe.WarehouseWorkerClient.invoke",
               return_value={
                 "duty":"accounting","database_identity_verified":True,
                 "query_execution_enabled":False
               }):
        result=probe("accounting",19083,
                     base64.b64encode(b"a"*32).decode())
    assert result["worker_process_verified"] is True
    assert result["query_execution_enabled"] is False


def test_probe_rejects_false_identity_or_wrong_duty_port():
    with pytest.raises(ValueError,match="DUTY_PORT"):
        probe("accounting",19081,base64.b64encode(b"a"*32).decode())
    with patch("tools.warehouse_local_worker_probe.WarehouseWorkerClient.invoke",
               return_value={"duty":"accounting",
                             "database_identity_verified":False,
                             "query_execution_enabled":False}):
        with pytest.raises(RuntimeError,match="IDENTITY_MISMATCH"):
            probe("accounting",19083,base64.b64encode(b"a"*32).decode())
