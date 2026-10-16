"""SQL-only guards around the existing immutable Gold reader, before full decoding."""
import hashlib
import io
import time
import pyarrow as pa
import pyarrow.parquet as pq
from app.processing.sql_policy import LIMITS
from app.processing.sql_validation import SqlBlocked
from app.storage.cumulative_gold_artifacts import CumulativeGoldArtifacts


def guard_inputs(store, limits=LIMITS):
    original_read=store.read
    transferred=0
    materialized=0
    started=time.monotonic()

    def check_time():
        if time.monotonic()-started>limits.input_seconds:
            raise SqlBlocked('INPUT_LIMIT','Input loading exceeded its time budget. No query was executed.')

    def read(key,max_bytes=None):
        nonlocal transferred
        check_time()
        remaining=limits.input_bytes-transferred
        if remaining<=0:raise SqlBlocked('INPUT_LIMIT','Query sources exceed the artifact byte budget.')
        maximum=min(limits.artifact_bytes,limits.footer_bytes) if key.endswith('.json') else limits.artifact_bytes
        try:body=original_read(key,max_bytes=min(max_bytes or maximum,maximum,remaining))
        except ValueError as exc:
            if str(exc)=='Artifact byte limit exceeded':raise SqlBlocked('INPUT_LIMIT','Query sources exceed the artifact byte budget.') from None
            raise
        transferred+=len(body)
        check_time()
        return body

    def frame(key,checksum):
        nonlocal materialized
        body=read(key)
        if hashlib.sha256(body).hexdigest()!=checksum:raise ValueError('Artifact integrity failed')
        if len(body)<12 or body[-4:]!=b'PAR1':raise ValueError('Unsupported query artifact format')
        footer_size=int.from_bytes(body[-8:-4],'little')
        if footer_size>limits.footer_bytes:raise SqlBlocked('INPUT_LIMIT','Query artifact metadata exceeds the input budget.')
        parquet=pq.ParquetFile(io.BytesIO(body))
        metadata=parquet.metadata
        if metadata.num_rows>limits.input_rows or metadata.num_columns>limits.input_columns or metadata.num_row_groups>limits.row_groups:
            raise SqlBlocked('INPUT_LIMIT','Query artifact rows, columns or row groups exceed the input budget.')
        decoded=sum(metadata.row_group(i).total_byte_size for i in range(metadata.num_row_groups))
        if decoded>limits.input_bytes-materialized:
            raise SqlBlocked('INPUT_LIMIT','Query artifact decoded size exceeds the input budget.')
        # Dictionary values can expand across rows. A batch can never repeat more
        # than this conservative decoded-byte estimate permits. Actual Arrow bytes
        # and prospective pandas object overhead are also checked before conversion.
        batch_rows=max(1,min(512,(limits.input_bytes-materialized)//max(1,decoded)))
        batches=[];arrow_bytes=0;rows=0
        for batch in parquet.iter_batches(batch_size=batch_rows,use_threads=False):
            check_time();rows+=batch.num_rows;arrow_bytes+=batch.nbytes
            estimate=arrow_bytes*4+rows*metadata.num_columns*64
            if estimate>limits.input_bytes-materialized or rows>limits.input_rows:
                raise SqlBlocked('INPUT_LIMIT','Query artifact expansion exceeds the materialized input budget.')
            batches.append(batch)
        table=pa.Table.from_batches(batches,schema=parquet.schema_arrow)
        result=table.to_pandas()
        materialized+=int(result.memory_usage(deep=True).sum())
        if materialized>limits.input_bytes:raise SqlBlocked('INPUT_LIMIT','Query sources exceed the materialized input budget.')
        check_time()
        return result

    class Reader:
        # Keep the original store unchanged, including shared fixture/storage clients.
        def read(self,key,max_bytes=None):return read(key,max_bytes)
        def frame(self,key,checksum):return frame(key,checksum)
        def validate(self,*args,**kwargs):return CumulativeGoldArtifacts.validate(self,*args,**kwargs)
    return Reader()
