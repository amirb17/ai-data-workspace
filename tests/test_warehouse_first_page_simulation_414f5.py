"""414F5 simulated bounded first-page retrieval with real isolated PostgreSQL."""
import pytest

from app.services import warehouse_sql_first_page_service as page
from app.services import warehouse_sql_attested_accounting_service as accounting
from app.services import warehouse_sql_result_eligibility_service as eligibility
from app.warehouse.query_result_pages import ResultPageConflict
from tests.test_warehouse_terminal_evidence_414f2 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    scalar, send_and_recover,
)

COLUMNS = (("session_id", "STRING"), ("energy_kwh", "NUMERIC"))


class FakeFirstPage:
    def __init__(self):
        self.calls=[]
        self.error=None
        self.before=None
        self.cursor=None
        self.malformed=False

    def get_first_page_once(self, *, project, location, job_id, max_results):
        self.calls.append((project,location,job_id,max_results))
        if self.before is not None:
            self.before()
        if self.error:
            raise self.error
        return {
            "job_id": "foreign" if self.malformed else job_id,
            "schema": [{"name": name, "type": typ} for name,typ in COLUMNS],
            "rows": [["S001","14.50"],["S002","9.25"]],
            "next_page_token": self.cursor,
        }


def read(s, client, schema=COLUMNS):
    return page.read_first_warehouse_page_simulated(
        attempt_id=s.a.attempt_id, approved_schema=schema, transport=client,
    )


def test_verified_success_fetches_one_bounded_page_and_no_provider_sql(pinned_db):
    s,lease,metadata=pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    client=FakeFirstPage()
    result=read(s,client)
    assert result.rows==(("S001","14.50"),("S002","9.25"))
    assert result.columns==("session_id","energy_kwh")
    assert not result.provider_has_more
    assert not result.provider_dispatched and not result.execution_authorized
    assert len(client.calls)==1
    assert client.calls[0][:3]==(lease.job.project,lease.job.location,lease.job.job_id)
    assert 1<=client.calls[0][3]<=100
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage")==1
    assert len(metadata.calls)==1


def test_unsettled_or_failed_job_rejected_before_result_network(pinned_db):
    s,lease,metadata=pinned_db
    client=FakeFirstPage()
    with pytest.raises(eligibility.ResultEligibilityConflict):
        read(s,client)
    assert client.calls==[]


@pytest.mark.parametrize("failure", [TimeoutError("secret"), ConnectionError("connection")])
def test_result_transport_error_is_bounded_and_redacted(pinned_db,failure):
    s,lease,metadata=pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    client=FakeFirstPage()
    client.error=failure
    with pytest.raises(page.ResultFetchUnavailable,match="RESULT_LOOKUP_UNAVAILABLE"):
        read(s,client)
    assert len(client.calls)==1


def test_wrong_job_result_is_rejected_and_never_returned(pinned_db):
    s,lease,metadata=pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    client=FakeFirstPage()
    client.malformed=True
    with pytest.raises(ResultPageConflict,match="JOB_OR_SCHEMA_MISMATCH"):
        read(s,client)
    assert len(client.calls)==1


def test_opaque_provider_cursor_is_not_exposed_to_user(pinned_db):
    s,lease,metadata=pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    client=FakeFirstPage()
    client.cursor="private-provider-cursor"
    result=read(s,client)
    assert result.provider_has_more
    assert "private-provider-cursor" not in str(result)
    assert not hasattr(result,"next_page_token")


def test_no_ambient_sdk_or_implicit_credentials(pinned_db):
    s,lease,metadata=pinned_db
    with pytest.raises(TypeError):
        read(s,object())
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage")==0
