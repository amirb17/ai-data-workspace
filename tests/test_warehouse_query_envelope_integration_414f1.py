"""414F1 integration: actual owned warehouse policy -> deterministic job envelope.

Uses disposable PostgreSQL and fake GET-only BigQuery. No real cloud actions,
provider submission, user-supplied SQL or public execute endpoint.
"""
from dataclasses import asdict

import pytest
from fastapi import HTTPException

from app.services import warehouse_sql_provider_policy_service as service
from app.services import warehouse_sql_send_recovery_service as journal
from tests.test_warehouse_sql_provider_policy_414e3 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, bound,
    scalar, change, request,
)


def inspect(s, token=None):
    return service.inspect_authenticated_query_envelope_without_dispatch(
        request=request(s.token if token is None else token),
        workspace_id=s.f.pg["workspace"], attempt_handle=s.a.attempt_handle,
    )


def test_authenticated_current_envelope_is_stable_non_dispatch_and_durable(unbound):
    s, _, client = unbound
    first = inspect(s)
    again = inspect(s)
    assert first == again
    assert first.classification == "ENVELOPE_REVIEWED_NO_DISPATCH"
    assert not first.execution_authorized
    assert not first.provider_dispatched
    assert first.job_id.startswith(f'dr_query_w{s.f.pg["workspace"]}_r{s.r}_')
    assert len(first.request_sha256) == 64
    assert set(asdict(first)) == {
        "attempt_handle", "job_id", "request_sha256", "classification",
    }
    assert scalar(s, "SELECT count(*) FROM warehouse_query_provider_policies") == 1
    assert scalar(s, "SELECT count(*) FROM warehouse_query_send_journal") == 2
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0
    assert not client.calls


def test_revoked_account_cannot_inspect_or_construct_new_envelope(unbound):
    s, _, client = unbound
    change(s, "account")
    with pytest.raises((HTTPException, ValueError)):
        inspect(s)
    assert scalar(s, "SELECT count(*) FROM warehouse_query_provider_policies") == 0
    assert not client.calls


def test_send_boundary_cannot_be_repackaged_as_new_request(unbound):
    s, lease, client = unbound
    inspect(s)
    journal.record_send_boundary(lease=lease, expected_revision=lease.revision)
    with pytest.raises(service.PolicyConflict, match="EXISTING_SEND_STATE_LOOKUP_ONLY"):
        inspect(s)
    assert scalar(s, "SELECT count(*) FROM warehouse_query_provider_policies") == 1
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0
    assert not client.calls
