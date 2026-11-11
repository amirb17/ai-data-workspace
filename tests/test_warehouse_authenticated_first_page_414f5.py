"""414F5 composed authenticated result read, fake transport and isolated PG."""
import pytest
from fastapi import HTTPException

from app.services import warehouse_sql_authenticated_first_page_service as first
from app.services import warehouse_sql_attested_accounting_service as accounting
from app.services.warehouse_sql_result_eligibility_service import ResultEligibilityConflict
from tests.test_warehouse_query_envelope_pinning_414f1 import request, change
from tests.test_warehouse_server_result_schema_414f5 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    PinnedPageClient,
)
from tests.test_warehouse_terminal_evidence_414f2 import send_and_recover


def call(s,transport,*,token=None,handle=None,workspace=None):
    return first.read_authenticated_first_warehouse_page_simulated(
        request=request(s.token if token is None else token),
        workspace_id=s.f.pg["workspace"] if workspace is None else workspace,
        attempt_handle=s.a.attempt_handle if handle is None else handle,
        transport=transport,
    )


def ready(s,lease):
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)


def client_for(s):
    from app.services.warehouse_sql_server_result_schema_service import inspect_server_result_schema
    return PinnedPageClient(inspect_server_result_schema(attempt_id=s.a.attempt_id).columns)


def test_signed_in_original_owner_receives_only_validated_page(pinned_db):
    s,lease,metadata=pinned_db
    ready(s,lease)
    transport=client_for(s)
    result=call(s,transport)
    assert result.attempt_handle==s.a.attempt_handle
    assert len(result.rows)==1 and len(result.columns)==len(result.rows[0])
    assert result.classification=="AUTHENTICATED_BOUNDED_FIRST_PAGE_SIMULATED"
    assert not result.provider_dispatched and not result.execution_authorized
    assert not hasattr(result,"job_id") and not hasattr(result,"next_page_token")
    assert len(transport.calls)==1
    assert transport.calls[0][:3]==(lease.job.project,lease.job.location,lease.job.job_id)
    assert len(metadata.calls)==1


@pytest.mark.parametrize("token,handle",[
    ("not-the-original-cookie",None),
    (None,"f"*32),
    (None,"bad-handle"),
])
def test_unauthenticated_or_wrong_attempt_denied_before_result_io(pinned_db,token,handle):
    s,lease,metadata=pinned_db
    ready(s,lease)
    transport=client_for(s)
    with pytest.raises((HTTPException,ValueError)):
        call(s,transport,token=token,handle=handle)
    assert not transport.calls


def test_unsettled_job_cannot_reach_provider_even_with_original_session(pinned_db):
    s,lease,metadata=pinned_db
    transport=client_for(s)
    with pytest.raises(ResultEligibilityConflict):
        call(s,transport)
    assert not transport.calls


def test_access_revoked_during_provider_response_must_drop_result(pinned_db):
    s,lease,metadata=pinned_db
    ready(s,lease)
    transport=client_for(s)
    original=transport.get_first_page_once

    def revoke_and_return(**kwargs):
        result=original(**kwargs)
        change(s,"account")
        return result

    transport.get_first_page_once=revoke_and_return
    with pytest.raises(HTTPException):
        call(s,transport)
    assert len(transport.calls)==1


def test_cross_workspace_denied_before_provider_io(pinned_db):
    s,lease,metadata=pinned_db
    ready(s,lease)
    transport=client_for(s)
    with pytest.raises((HTTPException,ValueError)):
        call(s,transport,workspace=s.f.pg["workspace"]+99999)
    assert not transport.calls
