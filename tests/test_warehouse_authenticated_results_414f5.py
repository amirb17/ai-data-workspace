"""414F5 original-session authentication gate, disposable PostgreSQL."""
import pytest
from fastapi import HTTPException
from app.services import warehouse_sql_authenticated_result_service as auth
from app.services import warehouse_sql_attested_accounting_service as accounting
from tests.test_warehouse_query_envelope_pinning_414f1 import request, change
from tests.test_warehouse_terminal_evidence_414f2 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    scalar, send_and_recover,
)


def read(s, token=None, workspace=None, handle=None):
    return auth.inspect_authenticated_warehouse_result_without_fetch(
        request=request(s.token if token is None else token),
        workspace_id=s.f.pg["workspace"] if workspace is None else workspace,
        attempt_handle=s.a.attempt_handle if handle is None else handle,
    )


def test_original_session_reads_settled_result_inspection_without_fetch(pinned_db):
    s,lease,client=pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    response=read(s)
    assert response.attempt_handle==s.a.attempt_handle
    assert response.classification=="AUTHORIZED_RESULT_INSPECTION_NO_FETCH"
    assert 1<=response.max_page_rows<=100
    assert not response.result_fetched and not response.provider_dispatched
    assert len(client.calls)==1


def test_unsettled_query_denied_even_with_correct_login(pinned_db):
    s,lease,client=pinned_db
    from app.services.warehouse_sql_result_eligibility_service import ResultEligibilityConflict
    with pytest.raises(ResultEligibilityConflict,match="SETTLED_SUCCESS_REQUIRED"):
        read(s)
    assert not client.calls


@pytest.mark.parametrize("handle",["other","f"*32])
def test_invalid_or_nonexistent_attempt_handle_denied(pinned_db,handle):
    s,lease,client=pinned_db
    with pytest.raises((ValueError,HTTPException)):
        read(s,handle=handle)
    assert not client.calls


def test_wrong_session_denied_even_when_attempt_id_is_known(pinned_db):
    s,lease,client=pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    with pytest.raises(HTTPException) as error:
        read(s,token="definitely-not-signed-in")
    assert error.value.status_code==401
    assert len(client.calls)==1


def test_revoked_checkpoint_cannot_be_used_to_inspect_results(pinned_db):
    s,lease,client=pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    change(s,"account")
    with pytest.raises(HTTPException):
        read(s)
    assert len(client.calls)==1


def test_wrong_workspace_has_no_cross_workspace_result_access(pinned_db):
    s,lease,client=pinned_db
    with pytest.raises((HTTPException,ValueError)):
        read(s,workspace=s.f.pg["workspace"]+99999)
    assert not client.calls
