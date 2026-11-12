"""Submission worker must not become a surrogate control/authentication role.

These checks are offline. They do not send a provider job, write a send
journal or grant access to canonical users/sessions/publications.
"""
from unittest.mock import patch

import pytest

from app.warehouse.worker_rpc_protocol import WorkerRpcRejected
from app.warehouse.worker_rpc_server import execute_operation
from tools.warehouse_worker_acl import READS, WRITES


SENSITIVE_CONTROL_READS = {
    ("public", "users"),
    ("public", "auth_sessions"),
    ("public", "workspaces"),
    ("public", "warehouse_resources"),
    ("public", "warehouse_publication_heads"),
    ("public", "warehouse_serving_materializations"),
    ("public", "warehouse_model_sources"),
    ("public", "datasets"),
    ("datarise_warehouse_dev", "warehouse_query_attempt_logical_drafts"),
}


def test_provider_cannot_record_boundary_before_cross_process_authorization():
    # Even a valid envelope is not itself a provider execution permit.
    # Any RPC that might record a send must fail before entering the service.
    with patch(
        "app.services.warehouse_sql_send_recovery_service"
        ".record_pinned_send_boundary_without_dispatch"
    ) as operation:
        with pytest.raises(WorkerRpcRejected,
                           match="PROVIDER_SEND_BOUNDARY_NOT_APPROVED"):
            execute_operation(
                "provider_submission","record_send_boundary",
                {"lease":{}, "expected_revision":1}
            )
        operation.assert_not_called()


def test_provider_role_never_reads_control_identity_or_publication_internals():
    assert not (SENSITIVE_CONTROL_READS & READS["provider_submission"])
    assert not (SENSITIVE_CONTROL_READS & set(WRITES["provider_submission"]))
    assert ("public","query_governance_reservations") not in WRITES[
        "provider_submission"
    ]


def test_submission_has_no_execution_or_provider_credentials_in_rpc_args():
    from app.warehouse.worker_rpc_protocol import DUTY_OPERATIONS
    assert "execute_sql" not in DUTY_OPERATIONS["provider_submission"]
    assert "submit_bigquery_job" not in DUTY_OPERATIONS["provider_submission"]
    assert "record_send_boundary" in DUTY_OPERATIONS["provider_submission"]
