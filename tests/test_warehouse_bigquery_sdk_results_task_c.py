"""Real installed SDK serialization, fake HTTP only; no ADC or cloud calls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud.bigquery import Client

from app.warehouse.query_bigquery_readonly_transport import (
    BigQuerySdkResultsConnection, ReadOnlyBigQueryFirstPage, ReviewedResultsConnection,
)
from app.warehouse.query_bigquery_first_page_adapter import BigQueryResultShapeError
from tests.test_warehouse_bigquery_first_page_adapter_414f5 import PROJECT, LOCATION, JOB, SCHEMA, raw
from app.warehouse.query_bigquery_first_page_adapter import validate_bigquery_first_page


class Http:
    def __init__(self, code=200, error=None):
        self.calls=[];self.code=code;self.error=error;self.is_mtls=False
        self._auth_request=SimpleNamespace(session=SimpleNamespace(close=lambda:None))
    def request(self, **kwargs):
        self.calls.append(kwargs)
        if self.error: raise self.error
        return SimpleNamespace(status_code=self.code,content=b'{}',headers={'content-type':'application/json'},
            json=lambda:raw(),request=SimpleNamespace(method='GET',url=kwargs['url']))
    def close(self): pass


def client(http):
    return Client(project=PROJECT,location=LOCATION,credentials=AnonymousCredentials(),_http=http)


def get(http):
    with client(http) as sdk:
        connection=BigQuerySdkResultsConnection(sdk)
        return ReadOnlyBigQueryFirstPage(ReviewedResultsConnection(PROJECT,LOCATION,connection)).get_query_results_once(
            project=PROJECT,location=LOCATION,job_id=JOB,max_results=10)


def test_real_sdk_get_wire_shape_without_unsupported_retry_argument():
    http=Http();response=get(http)
    assert validate_bigquery_first_page(project=PROJECT,location=LOCATION,job_id=JOB,
        approved_schema=SCHEMA,requested_rows=10,raw=response).rows==((7,'12.50',True,'Coffee'),)
    assert len(http.calls)==1
    call=http.calls[0]
    assert call['method']=='GET' and call['timeout']==10
    assert call['url'].startswith(f'https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/queries/{JOB}?')
    assert 'location=asia-south1' in call['url'] and 'maxResults=10' in call['url']
    assert call['data'] is None
    assert 'retry' not in call


@pytest.mark.parametrize('patch',[
    {'method':'POST'}, {'path':f'/projects/{PROJECT}/jobs'},
    {'path':f'/projects/foreign-project/queries/{JOB}'},
    {'retry':True}, {'timeout':60},
    {'query_params':{'location':LOCATION,'maxResults':101,'timeoutMs':1000}},
    {'query_params':{'location':LOCATION,'maxResults':True,'timeoutMs':1000}},
    {'query_params':{'location':'US','maxResults':10,'timeoutMs':1000}},
    {'query_params':{'location':LOCATION,'maxResults':10,'timeoutMs':1000,'pageToken':'internal'}},
])
def test_bridge_rejects_unreviewed_operation_scope_and_unbounded_reads(patch):
    http=Http()
    with client(http) as sdk:
        bridge=BigQuerySdkResultsConnection(sdk)
        args=dict(method='GET',path=f'/projects/{PROJECT}/queries/{JOB}',
            query_params=dict(location=LOCATION,maxResults=10,timeoutMs=1000),timeout=10,retry=None)
        args.update(deepcopy(patch))
        with pytest.raises(ValueError,match='EXACT_READ_ONLY'):
            bridge.api_request(**args)
    assert http.calls==[]


@pytest.mark.parametrize('code,error',[(403,None),(429,None),(503,None),(200,TimeoutError('private credential detail'))])
def test_http_permission_outage_timeout_is_redacted_and_not_sdk_retried(code,error):
    http=Http(code,error)
    with pytest.raises(BigQueryResultShapeError,match='RESULT_PROVIDER_READ_UNAVAILABLE') as caught:get(http)
    assert 'private' not in str(caught.value) and len(http.calls)==1


def test_client_scope_and_endpoint_drift_are_denied_before_io():
    http=Http()
    with client(http) as sdk:
        bridge=BigQuerySdkResultsConnection(sdk);sdk._connection.API_BASE_URL='https://untrusted.invalid'
        with pytest.raises(ValueError):
            bridge.api_request(method='GET',path=f'/projects/{PROJECT}/queries/{JOB}',
                query_params=dict(location=LOCATION,maxResults=10,timeoutMs=1000),timeout=10,retry=None)
    assert not http.calls


def test_plain_objects_cannot_be_used_as_sdk_clients():
    with pytest.raises(ValueError):BigQuerySdkResultsConnection(object())


def test_effective_mtls_endpoint_cannot_bypass_endpoint_pin():
    http=Http()
    with client(http) as sdk:
        bridge=BigQuerySdkResultsConnection(sdk)
        http.is_mtls=True;sdk._connection.API_BASE_MTLS_URL='https://untrusted.invalid'
        with pytest.raises(ValueError,match='EXACT_READ_ONLY'):
            bridge.api_request(method='GET',path=f'/projects/{PROJECT}/queries/{JOB}',
                query_params=dict(location=LOCATION,maxResults=10,timeoutMs=1000),timeout=10,retry=None)
    assert not http.calls
