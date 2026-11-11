"""414G warehouse result-readiness API, zero provider result IO."""
import pytest
from fastapi import HTTPException

from app.api import warehouse_catalog as api
from app.services import warehouse_sql_attested_accounting_service as accounting
from tests.test_warehouse_query_envelope_pinning_414f1 import request
from tests.test_warehouse_terminal_evidence_414f2 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    send_and_recover,
)


def route(s, *, token=None, handle=None, workspace=None):
    return api.query_result_readiness(
        workspace_id=s.f.pg["workspace"] if workspace is None else workspace,
        attempt_handle=s.a.attempt_handle if handle is None else handle,
        request=request(s.token if token is None else token),
    )


def test_authenticated_settled_result_status_is_safe_and_nonfetching(pinned_db):
    s,lease,client=pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    response=route(s)
    assert response == {
        "attempt_handle": s.a.attempt_handle,
        "status":"RESULT_ELIGIBLE",
        "max_page_rows":response["max_page_rows"],
        "results_available":False,
        "query_execution_enabled":False,
    }
    assert 1<=response["max_page_rows"]<=100
    assert lease.job.job_id not in str(response)
    assert len(client.calls)==1


def test_pending_results_get_409_and_never_access_provider(pinned_db):
    s,lease,client=pinned_db
    with pytest.raises(HTTPException) as error:
        route(s)
    assert error.value.status_code==409
    assert not client.calls


def test_unknown_attempt_returns_nonrevealing_error(pinned_db):
    s,lease,client=pinned_db
    with pytest.raises(HTTPException) as error:
        route(s,handle="f"*32)
    assert error.value.status_code in (400,404)
    assert not client.calls


def test_invalid_cookie_denies_result_api(pinned_db):
    s,lease,client=pinned_db
    with pytest.raises(HTTPException) as error:
        route(s,token="wrong-cookie")
    assert error.value.status_code==401
    assert not client.calls


def test_unowned_workspace_denied_before_provider_io(pinned_db):
    s,lease,client=pinned_db
    with pytest.raises(HTTPException) as error:
        route(s,workspace=s.f.pg["workspace"]+9999)
    assert error.value.status_code in (403,404)
    assert not client.calls


def test_result_readiness_route_is_registered_but_has_no_execution_or_result_path():
    from app.main import app
    from app.security.public_launch import http_routes
    # This app uses lazy router includes; app.routes can contain wrappers.
    installed=list(http_routes(app.router.routes))
    matches=[r for r in installed
        if getattr(r,"path","")=="/workspaces/{workspace_id}/warehouse/queries/{attempt_handle}/result-readiness"]
    assert len(matches)==1
    assert "GET" in matches[0].methods
    assert matches[0].endpoint is api.query_result_readiness
    assert not any(getattr(r,"path","").endswith("/warehouse/execute") for r in installed)
