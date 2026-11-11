"""414F5 simulated BigQuery first page bridge, fake provider only."""
from app.warehouse.query_bigquery_first_page_bridge import SimulatedBigQueryFirstPage
from tests.test_warehouse_bigquery_first_page_adapter_414f5 import (
    PROJECT,LOCATION,JOB,SCHEMA,raw,
)

class FakeBQ:
    def __init__(self, payload=None):
        self.calls=[]
        self.payload=raw() if payload is None else payload
    def get_query_results_once(self,*,project,location,job_id,max_results):
        self.calls.append((project,location,job_id,max_results))
        return self.payload


def test_bridge_executes_exactly_one_fake_result_get_and_returns_validated_shape():
    fake=FakeBQ()
    bridge=SimulatedBigQueryFirstPage(fake,SCHEMA)
    response=bridge.get_first_page_once(
        project=PROJECT,location=LOCATION,job_id=JOB,max_results=10,
    )
    assert fake.calls==[(PROJECT,LOCATION,JOB,10)]
    assert response["job_id"]==JOB
    assert response["schema"]==[{"name":name,"type":kind} for name,kind in SCHEMA]
    assert response["rows"]==[[7,"12.50",True,"Coffee"]]
    assert response["next_page_token"] is None


def test_bridge_returns_opaque_provider_cursor_to_backend_only():
    payload=raw()
    payload["pageToken"]="cursor-only-for-backend"
    bridge=SimulatedBigQueryFirstPage(FakeBQ(payload),SCHEMA)
    response=bridge.get_first_page_once(
        project=PROJECT,location=LOCATION,job_id=JOB,max_results=10,
    )
    assert response["next_page_token"]=="cursor-only-for-backend"
