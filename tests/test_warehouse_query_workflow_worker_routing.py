"""Workflow uses separately hosted recovery/accounting workers when supplied.

This is an integration seam test. It does not enable cloud execution or
circumvent provider send authorization.
"""
from types import SimpleNamespace
from unittest.mock import patch

from app.services import warehouse_query_workflow_service as workflow


def test_workflow_lease_uses_attester_worker_not_local_database():
    calls=[]
    class Router:
        def inspect_recovery(self, attempt):
            calls.append(("inspect",attempt))
            return SimpleNamespace(revision=4)
        def acquire_recovery(self,attempt,revision,seconds):
            calls.append(("acquire",attempt,revision,seconds))
            return SimpleNamespace(attempt_id=attempt,revision=revision+1)
    runtime=SimpleNamespace(worker_router=Router(),lease_seconds=25)
    with patch.object(workflow.recovery,"inspect_recovery") as local_inspect,patch.object(
        workflow.recovery,"acquire_recovery"
    ) as local_acquire:
        lease=workflow._lease(17,runtime)
    assert lease.revision==5
    assert calls==[("inspect",17),("acquire",17,4,25)]
    local_inspect.assert_not_called()
    local_acquire.assert_not_called()


def test_workflow_keeps_offline_injected_lease_fallback():
    runtime=SimpleNamespace(lease_seconds=30)
    with patch.object(workflow.recovery,"inspect_recovery",
        return_value=SimpleNamespace(revision=2)),patch.object(
        workflow.recovery,"acquire_recovery",
        return_value=SimpleNamespace(revision=3)
    ) as local:
        assert workflow._lease(18,runtime).revision==3
    assert local.call_args.kwargs == {
        "attempt_id":18,"expected_revision":2,"lease_seconds":30
    }


def test_runtime_rejects_untrusted_worker_router_shape():
    import pytest
    with pytest.raises(ValueError,match="Independent warehouse worker router"):
        workflow.InjectedQueryRuntime(
            metadata_client=None,estimator=None,submit_transport=None,
            results_transport=None,cancel_transport=None,
            worker_router=SimpleNamespace(),
        )
