"""Pure named result identity and connection boundary adversarial cases."""
from copy import deepcopy
from dataclasses import asdict
import pytest
from app.db import database
from app.db import warehouse_query_send_repository as repo
from app.warehouse.named_query_results import *
from app.warehouse.query_job_request import build_reviewed_query_job_request
from tests.test_warehouse_query_job_request_414f1 import policy,SQL


def expected():
    value=policy();value['contract_version']=2
    value['result']=dict(version=1,destination=result_identity(value['project'],5,value['job_id']),
        schema=[['line','INTEGER']],maximum_rows=5,maximum_storage_bytes=STORAGE_BYTES,
        workspace_storage_bytes=WORKSPACE_STORAGE_BYTES,read_until_ms=9999999999999,
        policy=asdict(NamedResultPolicy('reader@synthetic-project.iam.gserviceaccount.com','test-1')))
    return value


def test_named_request_is_deterministic_no_warehouse_overwrite_and_legacy_unchanged():
    first=build_reviewed_query_job_request(expected=expected(),compiled_sql=SQL)
    assert first==build_reviewed_query_job_request(expected=expected(),compiled_sql=SQL)
    query=first.body['configuration']['query']
    assert query['destinationTable']['datasetId']=='dr_results_dev_w5'
    assert query['createDisposition']=='CREATE_IF_NEEDED' and query['writeDisposition']=='WRITE_EMPTY'
    assert 'destinationTable' not in build_reviewed_query_job_request(expected=policy(),compiled_sql=SQL).body['configuration']['query']


@pytest.mark.parametrize('field,value',[
    ('maximum_storage_bytes',STORAGE_BYTES+1),('workspace_storage_bytes',WORKSPACE_STORAGE_BYTES+1),
    ('maximum_rows',501),('schema',[['name','STRING']]),('version',2),('read_until_ms',0),
    ('destination',{'projectId':'synthetic-project','datasetId':'dr_dev_w5','tableId':'owned'}),
])
def test_named_contract_cannot_widen_or_replace_destination(field,value):
    original=expected();original['result'][field]=value
    with pytest.raises(ValueError):build_reviewed_query_job_request(expected=original,compiled_sql=SQL)


@pytest.mark.parametrize('duty',['query_control','provider_submission','recovery_attestation','accounting','result_authorization','publication','retention_inspection'])
def test_process_boundary_cannot_route_another_duty_or_fall_back(duty,monkeypatch):
    calls=[]
    def factory():calls.append(True);raise RuntimeError('isolated factory unavailable')
    def forbidden(*args,**kwargs):raise AssertionError('Default DSN fallback forbidden')
    monkeypatch.setattr(database.psycopg,'connect',forbidden)
    with database.connection_boundary(duty,factory):
        other='query_control' if duty!='query_control' else 'accounting'
        with pytest.raises(RuntimeError,match='Cross-duty'):
            with repo.transaction(duty=other):pass
        assert not calls
        with pytest.raises(RuntimeError,match='factory unavailable'):
            with repo.transaction(duty=duty):pass
    assert len(calls)==1
    assert database._connection_boundary.get() is None
