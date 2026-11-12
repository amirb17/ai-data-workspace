"""The production acceptance gate must never report fake E2E readiness."""
from unittest.mock import patch

from tools import warehouse_live_e2e_acceptance as gate


def test_live_e2e_gate_fails_closed_when_runtime_cannot_execute():
    with patch.object(gate,"permissions",return_value={
        "workers":{
            "provider_submission":{"permissions_ready":False,"missing_privileges":[{"relation":"x"}]},
            "recovery_attestation":{"permissions_ready":True,"missing_privileges":[]},
            "accounting":{"permissions_ready":True,"missing_privileges":[]},
            "result_authorization":{"permissions_ready":True,"missing_privileges":[]},
        }
    }):
        report=gate.inspect(object())
    assert report["live_e2e_ready"] is False
    assert report["query_execution_enabled"] is False
    assert "WORKER_PRIVILEGES_INCOMPLETE:provider_submission" in report["blockers"]
    assert "API_QUERY_RUNTIME_DISABLED" in report["blockers"]
    assert "ATOMIC_PROVIDER_SEND_AUTHORIZATION_NOT_IMPLEMENTED" in report["blockers"]


def test_acceptance_gate_does_not_enable_live_send_or_invoke_bigquery():
    with patch.object(gate.workflow,"get_query_runtime",side_effect=Exception(
            "No live provider runtime")), patch.object(
        gate,"execute_operation",side_effect=gate.WorkerRpcRejected(
            "PROVIDER_SEND_BOUNDARY_NOT_APPROVED")
    ) as call:
        blockers=gate.runtime_blockers()
    assert "API_QUERY_RUNTIME_UNAVAILABLE" in blockers
    assert "ATOMIC_PROVIDER_SEND_AUTHORIZATION_NOT_IMPLEMENTED" in blockers
    call.assert_called_once_with("provider_submission","record_send_boundary",{})
