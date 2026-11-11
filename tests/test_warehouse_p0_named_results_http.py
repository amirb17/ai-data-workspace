"""P0 complete HTTP result boundary using named-result C2 fixtures.

All provider responses are synthetic; production execution stays disabled.
"""
import pytest

from tests.test_warehouse_named_results_c2 import (
    named, pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    api_case, post, admit,
)


def _completed(s):
    handle=admit(s)
    response=post(s, f"/{handle}/execute")
    assert response.status_code==200,response.text
    assert response.json()["status"]=="SUCCEEDED"
    return handle


@pytest.mark.parametrize("failure",[
    "row_overdelivery",
    "oversized_provider_metadata",
    "oversized_cell",
    "nested_cell",
    "nonfinite_provider_metadata",
])
def test_named_result_http_rejects_unsafe_provider_response(named,monkeypatch,failure):
    s=named
    handle=_completed(s)
    original=s.reader.get_query_results_once

    def unsafe_page(**kwargs):
        result=original(**kwargs)
        if failure=="row_overdelivery":
            result["rows"]*=101
        elif failure=="oversized_provider_metadata":
            result["etag"]="a"*(256*1024+1)
        elif failure=="oversized_cell":
            result["rows"][0]["f"][0]["v"]="1"*9000
        elif failure=="nested_cell":
            result["rows"][0]["f"][0]["v"]={"nested":"1"}
        else:
            result["totalBytesProcessed"]=float("nan")
        return result

    monkeypatch.setattr(s.reader,"get_query_results_once",unsafe_page)
    response=s.client.get(s.base+"/"+handle+"/results")
    assert response.status_code in (400,409,503),response.text
    assert "rows" not in response.json()
    assert "1"*200 not in response.text


def test_named_result_http_never_returns_raw_provider_page_token(named,monkeypatch):
    s=named
    handle=_completed(s)
    original=s.reader.get_query_results_once

    def with_more(**kwargs):
        result=original(**kwargs)
        result["pageToken"]="private-provider-cursor"
        result["totalRows"]="500"
        return result

    monkeypatch.setattr(s.reader,"get_query_results_once",with_more)
    response=s.client.get(s.base+"/"+handle+"/results")
    assert response.status_code==200,response.text
    value=response.json()
    assert value["rows"]==[[1],[2]]
    assert value["has_more"] is True
    assert len(value["rows"])<=100
    assert "private-provider-cursor" not in response.text
    assert "pageToken" not in response.text
    assert "next_page_token" not in response.text
    assert "dr_results_" not in response.text


def test_http_sql_without_limit_and_wildcard_cannot_admit(named):
    s=named
    for index,sql in enumerate((
        f"SELECT * FROM model_{s.f.model}",
        f"SELECT line FROM model_{s.f.model}",
        f"SELECT line FROM model_{s.f.model} LIMIT 1000000",
    )):
        response=post(s,model_id=s.f.model,sql=sql,idempotency_key=f"p0-unsafe-admission-{index:04}")
        assert response.status_code in (400,422),response.text
    assert not s.fake.inserts
