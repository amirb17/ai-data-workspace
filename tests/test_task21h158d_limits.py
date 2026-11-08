"""Application resource rejection, not OS/Fargate resource isolation evidence."""
import io
import pytest
from app.processing_worker.workload_limits import (
    csv_rows,LimitedClient,WorkloadRejected,scratch_usage,RAW_BYTES,CELL_BYTES,
    RECORD_BYTES,OBJECT_BYTES,ARTIFACTS,retained_rows,
)


def test_csv_exact_boundary_preserves_lexical_money():
    payload=b'id,amount\n'+b'1,9007199254740993.01\n'*1000
    assert csv_rows(payload)==1000
    assert b'9007199254740993.01' in payload


@pytest.mark.parametrize('payload',[
    b'',b'\xff\n',b'a,a\n1,2\n',b'id\n'+b'1\n'*1001,
    (','.join('c'+str(i) for i in range(65))+'\n').encode(),
    b'c\n'+b'a'*(CELL_BYTES+1),b'c\n"unterminated',
    b'a,b\n1\n',b'x'* (RAW_BYTES+1),
    b'a,b,c,d,e\n'+b','.join([b'"'+b'\n'*60000+b'"']*5),
],ids=['empty','encoding','duplicate-header','rows','columns','cell','quotes','shape','bytes','multiline-record'])
def test_invalid_or_oversized_csv_rejected(payload):
    with pytest.raises(WorkloadRejected):csv_rows(payload)


def test_byte_limits_account_for_utf8_not_characters():
    assert csv_rows(('c\n'+('é'*(CELL_BYTES//2))).encode())==1
    with pytest.raises(WorkloadRejected):csv_rows(('c\n'+('é'*(CELL_BYTES//2+1))).encode())


def parquet(rows,columns=1):
    import pyarrow as pa,pyarrow.parquet as pq
    stream=io.BytesIO();pq.write_table(pa.table({f'c{i}':list(range(rows)) for i in range(columns)}),stream)
    return stream.getvalue()


@pytest.mark.parametrize('rows,columns',[(1001,1),(1,65)])
def test_parquet_metadata_limits_before_downstream_decode(rows,columns):
    class Client:
        def put_object(self,**_):raise AssertionError('Must reject before writing')
    client=LimitedClient(Client())
    with pytest.raises(WorkloadRejected):client.put_object(Key='synthetic.parquet',Body=parquet(rows,columns))


def test_object_count_and_body_close_are_bounded():
    class Client:
        def put_object(self,**_):return {}
        def get_object(self,**_):return dict(Body=body)
    client=LimitedClient(Client())
    for i in range(ARTIFACTS):client.put_object(Key=str(i),Body=b'x')
    with pytest.raises(WorkloadRejected):client.put_object(Key='overflow',Body=b'x')
    body=io.BytesIO(b'x'*(OBJECT_BYTES+1))
    with pytest.raises(WorkloadRejected):client.get_object(Key='oversized')
    assert body.closed


def test_scratch_scan_rejects_oversize_without_deleting(tmp_path):
    path=tmp_path/'synthetic-sparse'
    with path.open('wb') as f:f.truncate(128*1024*1024+1)
    with pytest.raises(WorkloadRejected):scratch_usage(tmp_path)
    assert path.exists()


@pytest.mark.parametrize('retained,incoming,stage',[(1001,0,'GOLD'),(1000,1,'SILVER'),(999,2,'SILVER'),(-1,0,'BRONZE')])
def test_retained_bound_counts_inactive_rows_and_worst_case_additions(retained,incoming,stage):
    with pytest.raises(WorkloadRejected):retained_rows(retained,incoming,stage)
    assert retained_rows(999,1,'SILVER') is None


def test_applied_receipt_recovery_does_not_count_source_twice():
    assert retained_rows(1000,1000,'SILVER',already_applied=True) is None
