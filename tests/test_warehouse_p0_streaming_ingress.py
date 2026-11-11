"""P0 pre-JSON-decoding ingress cap; all HTTP calls are offline fakes."""
import json
from urllib.parse import urlencode
from unittest.mock import Mock

import pytest
import requests

from app.warehouse.query_bigquery_first_page_adapter import (
    BigQueryResultShapeError, validate_bigquery_first_page,
)
from app.warehouse.query_bigquery_readonly_transport import (
    ReadOnlyBigQueryFirstPage, ReviewedResultsConnection,
)
from app.warehouse.query_bigquery_streaming_ingress import (
    BoundedStreamingBigQueryResultsConnection,
)
from tests.test_warehouse_bigquery_first_page_adapter_414f5 import (
    PROJECT, LOCATION, JOB, SCHEMA, raw,
)

BASE = f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/queries/{JOB}"


class Response:
    def __init__(self, *, chunks=None, status=200, headers=None, url=None, fault=None):
        self.status_code=status
        self.headers={"content-type":"application/json"} if headers is None else headers
        self.url=url or BASE
        self.chunks=[json.dumps(raw()).encode()] if chunks is None else chunks
        self.fault=fault
        self.closed=False
        self.chunks_seen=0

    def iter_content(self, *, chunk_size):
        assert chunk_size<=16*1024
        for chunk in self.chunks:
            self.chunks_seen+=1
            if self.fault and self.chunks_seen==2:
                raise self.fault
            yield chunk

    def close(self):
        self.closed=True


def harness(response=None):
    s=requests.Session()
    s.trust_env=False
    s.headers["Authorization"]="Bearer "+"x"*32
    response=response or Response()
    calls=[]

    def request(method,url,**kwargs):
        calls.append((method,url,kwargs))
        return response

    s.request=request
    adapter=BoundedStreamingBigQueryResultsConnection(project=PROJECT,location=LOCATION,session=s)
    reader=ReadOnlyBigQueryFirstPage(ReviewedResultsConnection(PROJECT,LOCATION,adapter))
    return s,reader,response,calls


def read(reader):
    return reader.get_query_results_once(
        project=PROJECT,location=LOCATION,job_id=JOB,max_results=10,
    )


def test_valid_stream_one_get_exact_scope_and_real_page_decode():
    s,reader,response,calls=harness()
    result=read(reader)
    page=validate_bigquery_first_page(
        project=PROJECT,location=LOCATION,job_id=JOB,approved_schema=SCHEMA,
        requested_rows=10,raw=result,
    )
    assert page.rows==((7,"12.50",True,"Coffee"),)
    assert response.closed and len(calls)==1
    method,url,kwargs=calls[0]
    assert method=="GET" and url==BASE
    assert kwargs["params"]=={"location":LOCATION,"maxResults":10,"timeoutMs":1000}
    assert kwargs["stream"] is True and kwargs["allow_redirects"] is False
    assert kwargs["timeout"]==(5,10) and kwargs["headers"]["Accept-Encoding"]=="identity"
    s.close()


@pytest.mark.parametrize("case",[
    "content_length", "actual_body", "huge_single_chunk",
    "redirect", "non_json", "gzip", "wrong_host", "malformed_json",
    "scalar_json", "stream_failure", "provider_error",
])
def test_oversized_and_invalid_http_never_return_decoded_json(case):
    if case=="content_length":
        response=Response(chunks=[b"{}"],headers={"content-type":"application/json","content-length":"262145"})
    elif case=="actual_body":
        response=Response(chunks=[b" "*16384]*17)
    elif case=="huge_single_chunk":
        response=Response(chunks=[b"x"*300000])
    elif case=="redirect":
        response=Response(status=302)
    elif case=="non_json":
        response=Response(headers={"content-type":"text/html"})
    elif case=="gzip":
        response=Response(headers={"content-type":"application/json","content-encoding":"gzip"})
    elif case=="wrong_host":
        response=Response(url="https://attacker.invalid/bigquery/v2")
    elif case=="malformed_json":
        response=Response(chunks=[b'{"broken"'])
    elif case=="scalar_json":
        response=Response(chunks=[b'[]'])
    elif case=="stream_failure":
        response=Response(chunks=[b'{',b'}'],fault=TimeoutError("secret wire error"))
    else:
        response=Response(status=403)
    s,reader,_,calls=harness(response)
    with pytest.raises(BigQueryResultShapeError):
        read(reader)
    assert response.closed and len(calls)==1
    if case=="content_length":
        assert response.chunks_seen==0
    if case=="actual_body":
        assert response.chunks_seen==17
    if case=="huge_single_chunk":
        assert response.chunks_seen==1
    s.close()


@pytest.mark.parametrize("change",["trust_env","auth","retries","cookies","missing_bearer","hook"])
def test_invalid_session_fails_before_network(change):
    s=requests.Session()
    s.trust_env=False
    s.headers["Authorization"]="Bearer "+"x"*32
    if change=="trust_env":s.trust_env=True
    elif change=="auth":s.auth=("person","password")
    elif change=="retries":s.get_adapter("https://").max_retries.total=2
    elif change=="cookies":s.cookies.set("secret","value")
    elif change=="missing_bearer":s.headers.pop("Authorization")
    else:s.hooks["response"].append(lambda r,*a,**kw:r)
    with pytest.raises(ValueError):
        BoundedStreamingBigQueryResultsConnection(project=PROJECT,location=LOCATION,session=s)
    s.close()


def test_client_scope_and_cursor_cannot_inject_another_request():
    s,reader,_,calls=harness()
    with pytest.raises(ValueError):
        reader.get_query_results_once(project=PROJECT,location=LOCATION,job_id=JOB,max_results=101)
    with pytest.raises(ValueError):
        reader.get_query_results_once(project="another-project",location=LOCATION,job_id=JOB,max_results=10)
    assert calls==[]
    s.close()


def test_session_rechecked_for_retry_policy_before_network():
    s,reader,_,calls=harness()
    s.get_adapter("https://").max_retries.total=1
    with pytest.raises(BigQueryResultShapeError,match="RESULT_PROVIDER_READ_UNAVAILABLE"):
        read(reader)
    assert calls==[]
    s.close()
