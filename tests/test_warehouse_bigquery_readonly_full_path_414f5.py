"""414F5 full backend fake-BigQuery GET path after authenticated settlement.

Uses the exact production-shaped read-only request boundary with injected
fake connection; no GCP credentials or live provider operations.
"""
from app.services import warehouse_sql_authenticated_first_page_service as authpage
from app.services import warehouse_sql_attested_accounting_service as accounting
from app.services.warehouse_sql_server_result_schema_service import inspect_server_result_schema
from app.warehouse.query_bigquery_first_page_bridge import SimulatedBigQueryFirstPage
from app.warehouse.query_bigquery_readonly_transport import (
    ReadOnlyBigQueryFirstPage,ReviewedResultsConnection,
)
from tests.test_warehouse_query_envelope_pinning_414f1 import request
from tests.test_warehouse_terminal_evidence_414f2 import (
    pg,modeling_pg,fact_provider_pg,genuinely_served_query,
    served,checkpointed,prepared,lookup,unbound,pinned_db,
    send_and_recover,
)


class FakeREST:
    def __init__(self, payload):
        self.payload=payload
        self.calls=[]

    def api_request(self,**kwargs):
        self.calls.append(kwargs)
        return self.payload


def payload_for(lease,fields):
    from tests.test_warehouse_server_result_schema_414f5 import typed_value
    values=[]
    kinds={"INTEGER":"INT64","BOOLEAN":"BOOL"}
    for _,kind in fields:
        val=typed_value(kind)
        if kind=="BOOLEAN":val="true" if val else "false"
        elif kind=="INTEGER":val=str(val)
        values.append({"v":val})
    return {
        "jobReference":{"projectId":lease.job.project,"location":lease.job.location,"jobId":lease.job.job_id},
        "jobComplete":True,
        "schema":{"fields":[{"name":name,"type":kinds.get(kind,kind),"mode":"NULLABLE"} for name,kind in fields]},
        "rows":[{"f":values}],
    }


def test_authenticated_original_session_reads_one_validated_fake_bq_rest_page(pinned_db):
    s,lease,metadata=pinned_db
    send_and_recover(s,lease)
    accounting.settle_verified_warehouse_terminal(attempt_id=s.a.attempt_id)
    schema=inspect_server_result_schema(attempt_id=s.a.attempt_id).columns
    rest=FakeREST(payload_for(lease,schema))
    provider=ReadOnlyBigQueryFirstPage(ReviewedResultsConnection(
        lease.job.project,lease.job.location,rest,
    ))
    bridge=SimulatedBigQueryFirstPage(provider,schema)
    result=authpage.read_authenticated_first_warehouse_page_simulated(
        request=request(s.token),workspace_id=s.f.pg["workspace"],
        attempt_handle=s.a.attempt_handle,transport=bridge,
    )
    assert result.columns==tuple(name for name,_ in schema)
    assert len(result.rows)==1
    assert len(rest.calls)==1
    assert rest.calls[0]["method"]=="GET"
    assert rest.calls[0]["retry"] is None
    assert rest.calls[0]["path"]==f"/projects/{lease.job.project}/queries/{lease.job.job_id}"
    assert len(metadata.calls)==1
    assert not result.execution_authorized and not result.provider_dispatched
    assert not hasattr(result,"job_id") and not hasattr(result,"next_page_token")
