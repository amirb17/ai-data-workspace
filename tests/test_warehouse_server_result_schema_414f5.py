"""414F5 server-owned exact projection tests; disposable PostgreSQL only."""
import pytest

from app.services import warehouse_sql_server_result_schema_service as schema
from app.services import warehouse_sql_first_page_service as first
from app.services import warehouse_sql_attested_accounting_service as accounting
from app.warehouse.query_result_pages import ResultPageConflict
from tests.test_warehouse_terminal_evidence_414f2 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    scalar, send_and_recover,
)

def typed_value(typ):
    return {
        "STRING":"alpha", "INTEGER":7,"NUMERIC":"2.75",
        "BOOLEAN":True,"DATE":"2026-11-11",
        "TIMESTAMP":"2026-11-11T10:00:00Z",
    }[typ]


class PinnedPageClient:
    def __init__(self, columns):
        self.columns=columns
        self.calls=[]
        self.wrong=False

    def get_first_page_once(self,*,project,location,job_id,max_results):
        self.calls.append((project,location,job_id,max_results))
        return {
            "job_id":job_id,
            "schema":[{"name":n,"type":t} for n,t in self.columns],
            "rows":[[typed_value(typ) for _,typ in self.columns]],
            "next_page_token":None,
        }


def ready(s,lease):
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)


def test_server_schema_matches_persisted_approved_query_projection(pinned_db):
    s,lease,metadata=pinned_db
    result=schema.inspect_server_result_schema(attempt_id=s.a.attempt_id)
    assert result.attempt_id==s.a.attempt_id
    assert 1<=len(result.columns)<=32
    assert len({name for name,_ in result.columns})==len(result.columns)
    assert all(typ in ("STRING","INTEGER","NUMERIC","BOOLEAN","DATE","TIMESTAMP") for _,typ in result.columns)
    assert not result.provider_dispatched


def test_result_page_uses_server_columns_without_callers_supplying_them(pinned_db):
    s,lease,metadata=pinned_db
    ready(s,lease)
    original=schema.inspect_server_result_schema(attempt_id=s.a.attempt_id)
    client=PinnedPageClient(original.columns)
    page=first.read_first_warehouse_page_server_pinned_simulated(
        attempt_id=s.a.attempt_id,transport=client,
    )
    assert page.columns==tuple(name for name,_ in original.columns)
    assert page.rows==(tuple(typed_value(typ) for _,typ in original.columns),)
    assert len(client.calls)==1
    assert client.calls[0][:3]==(lease.job.project,lease.job.location,lease.job.job_id)
    assert len(metadata.calls)==1


def test_provider_schema_substitution_fails_closed(pinned_db):
    s,lease,metadata=pinned_db
    ready(s,lease)
    original=schema.inspect_server_result_schema(attempt_id=s.a.attempt_id)
    client=PinnedPageClient(original.columns)
    client.columns=(("foreign_column","STRING"),)
    with pytest.raises(ResultPageConflict,match="JOB_OR_SCHEMA_MISMATCH"):
        first.read_first_warehouse_page_server_pinned_simulated(
            attempt_id=s.a.attempt_id,transport=client,
        )
    assert len(client.calls)==1


def test_unsettled_attempt_never_contacts_result_provider(pinned_db):
    s,lease,metadata=pinned_db
    original=schema.inspect_server_result_schema(attempt_id=s.a.attempt_id)
    client=PinnedPageClient(original.columns)
    from app.services.warehouse_sql_result_eligibility_service import ResultEligibilityConflict
    with pytest.raises(ResultEligibilityConflict,match="SETTLED_SUCCESS_REQUIRED"):
        first.read_first_warehouse_page_server_pinned_simulated(
            attempt_id=s.a.attempt_id,transport=client,
        )
    assert not client.calls


def test_wrong_or_missing_draft_identity_rejected_before_result_io(pinned_db, monkeypatch):
    s,lease,metadata=pinned_db
    real=schema.repo.one
    def substitute(conn,sql,params=()):
        row=real(conn,sql,params)
        if "FROM warehouse_query_attempt_logical_drafts" in sql and row:
            return {**row,"projected_columns":["unapproved_column"]}
        return row
    with monkeypatch.context() as patch:
        patch.setattr(schema.repo,"one",substitute)
        with pytest.raises(schema.ResultSchemaConflict,match="APPROVED_PROJECTION"):
            schema.inspect_server_result_schema(attempt_id=s.a.attempt_id)
