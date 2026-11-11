"""414F5 result page parser: zero network, exact schema, bounded output."""
from copy import deepcopy
import pytest
from app.warehouse.query_result_pages import (
    validate_result_page, ResultPageConflict, MAX_PAGE_ROWS,
)

JOB = "dr_query_w5_r7_" + "a"*32
SCHEMA = (("order_id", "INTEGER"), ("price", "NUMERIC"), ("valid", "BOOLEAN"))


def example():
    return {
        "job_id": JOB,
        "schema": [{"name": n, "type": t} for n,t in SCHEMA],
        "rows": [[1,"10.25",True],[2,None,False]],
        "next_page_token": None,
    }


def parse(response=None, schema=SCHEMA, count=10, job=JOB):
    return validate_result_page(
        job_id=job, approved_schema=schema,
        response=example() if response is None else response,
        requested_rows=count,
    )


def test_exact_bounded_result_page_is_immutable_and_typed():
    result=parse()
    assert result.job_id==JOB
    assert result.columns==("order_id","price","valid")
    assert result.rows==((1,"10.25",True),(2,None,False))
    assert result.next_page_token is None and result.bytes_encoded > 0
    assert not result.truncated


@pytest.mark.parametrize("change", [
    lambda r: r.update(job_id="wrong"),
    lambda r: r["schema"][0].update(type="STRING"),
    lambda r: r["schema"].append({"name":"foreign","type":"STRING"}),
    lambda r: r["rows"].append(["not integer","10",True]),
    lambda r: r["rows"].append([4,"123",1]),
    lambda r: r["rows"].append([3,{"data":"unsafe"},True]),
    lambda r: r["rows"].append([3,"4",True,"extra"]),
    lambda r: r.update(next_page_token=42),
    lambda r: r.update(next_page_token="x"*2049),
    lambda r: r.update(extra="injection"),
])
def test_drifted_or_unsafe_page_rejected(change):
    r=example();change(r)
    with pytest.raises(ResultPageConflict):
        parse(r)


def test_requested_row_ceiling_rejects_extra_rows():
    with pytest.raises(ResultPageConflict,match="ROW_LIMIT"):
        parse(count=1)
    with pytest.raises(ResultPageConflict,match="BOUNDS"):
        parse(count=MAX_PAGE_ROWS+1)


def test_large_page_and_nan_are_fail_closed():
    r=example();r["rows"]=[[1,"z"*9000,True]]
    with pytest.raises(ResultPageConflict):
        parse(r)
    r=example();r["rows"]=[[1,"2",True]]*100
    # Use large but valid scalar values to exceed the full encoded ceiling.
    r["schema"]=[{"name":"a","type":"STRING"}]
    r["rows"]=[["a"*8000] for _ in range(100)]
    with pytest.raises(ResultPageConflict,match="PAYLOAD_LIMIT"):
        parse(r,schema=(("a","STRING"),),count=100)
    r=example();r["schema"]=[{"name":"x","type":"FLOAT"}]
    r["rows"]=[[float("nan")]]
    with pytest.raises(ResultPageConflict,match="ENCODING"):
        parse(r,schema=(("x","FLOAT"),))


@pytest.mark.parametrize("schema", [
    (("x","RECORD"),),
    (("same","STRING"),("SAME","STRING")),
    (("1bad","STRING"),),
    (),
])
def test_invalid_approved_schema_rejected(schema):
    with pytest.raises(ResultPageConflict):
        parse(schema=schema)


def test_valid_opaque_cursor_is_held_for_server_only_usage():
    r=example()
    r["next_page_token"]="opaque-provider-cursor"
    assert parse(r).next_page_token=="opaque-provider-cursor"
