"""Small-input admission/I/O limits; no business transforms or OS isolation.

Use only around an existing trusted IntegratedExecutor in server composition.
Checks can reject an operation; they do not truncate, round or reinterpret data.
"""
import csv
import io
import os
from pathlib import Path


MIB=1024*1024
RAW_BYTES=2*MIB
ROWS=1000
COLUMNS=64
CELL_BYTES=64*1024
RECORD_BYTES=256*1024
OBJECT_BYTES=32*MIB
IO_BYTES=128*MIB
ARTIFACTS=16
SCRATCH_BYTES=128*MIB
MEMORY_BYTES=512*MIB


class WorkloadRejected(ValueError):pass


def retained_rows(retained,incoming,stage,*,already_applied=False):
    if type(retained) is not int or type(incoming) is not int or min(retained,incoming)<0:
        raise WorkloadRejected('Invalid retained-state accounting')
    if retained>ROWS or (stage=='SILVER' and not already_applied and retained+incoming>ROWS):
        raise WorkloadRejected('Retained-state bound')


def csv_rows(payload):
    if not isinstance(payload,bytes) or not 0<len(payload)<=RAW_BYTES:raise WorkloadRejected('Raw byte bound')
    try:
        text=payload.decode('utf-8-sig')
        # Physical line cap before csv can allocate an enormous multi-line cell.
        if any(len(line.encode('utf-8'))>RECORD_BYTES for line in text.splitlines()):raise WorkloadRejected('Record byte bound')
        lines=text.splitlines(keepends=True);offsets=[0]
        for line in lines:offsets.append(offsets[-1]+len(line.encode('utf-8')))
        reader=csv.reader(lines,strict=True);header=next(reader);previous=reader.line_num
        if offsets[previous]>RECORD_BYTES:raise WorkloadRejected('Record byte bound')
        if not 0<len(header)<=COLUMNS or len(set(header))!=len(header):raise WorkloadRejected('Source column bound')
        count=0
        for values in [header]:
            if any(len(v.encode('utf-8'))>CELL_BYTES for v in values):raise WorkloadRejected('Cell byte bound')
        for count,values in enumerate(reader,1):
            if offsets[reader.line_num]-offsets[previous]>RECORD_BYTES:raise WorkloadRejected('Record byte bound')
            previous=reader.line_num
            if count>ROWS or len(values)!=len(header):raise WorkloadRejected('Source row/shape bound')
            if any(len(v.encode('utf-8'))>CELL_BYTES for v in values):raise WorkloadRejected('Cell byte bound')
            if sum(len(v.encode('utf-8')) for v in values)>RECORD_BYTES:raise WorkloadRejected('Record byte bound')
        return count
    except (UnicodeError,csv.Error,StopIteration) as error:
        raise WorkloadRejected('Bounded UTF-8 CSV required') from error


class LimitedClient:
    """All stage/incremental client reads/writes share one per-job budget.

    Encoded bodies are bounded before Parquet inspection; metadata is inspected
    before downstream pandas decoding. Uncompressed Parquet size is an estimate,
    not a bound on pandas/Arrow native memory or hostile-parser CPU usage.
    """
    def __init__(self,client):self.client=client;self.io=0;self.decoded=0;self.writes=set()

    def _check(self,payload):
        if not isinstance(payload,bytes) or not 0<len(payload)<=OBJECT_BYTES:raise WorkloadRejected('Object byte bound')
        self.io+=len(payload)
        if self.io>IO_BYTES:raise WorkloadRejected('Stage I/O bound')
        if payload.startswith(b'PAR1'):
            import pyarrow.parquet as pq
            parquet=pq.ParquetFile(io.BytesIO(payload));metadata=parquet.metadata
            business=[n for n in parquet.schema_arrow.names if not n.startswith(('_source_','_bronze_','_dataset_','_is_','_effective_','_last_'))]
            decoded=sum(metadata.row_group(r).total_byte_size for r in range(metadata.num_row_groups))
            self.decoded+=decoded
            if metadata.num_rows>ROWS or len(business)>COLUMNS or metadata.num_columns>COLUMNS+32 or decoded>OBJECT_BYTES or self.decoded>IO_BYTES:
                raise WorkloadRejected('Decoded stage bound')

    def head_object(self,**kwargs):return self.client.head_object(**kwargs)

    def get_object(self,**kwargs):
        response=self.client.get_object(**kwargs);body=response['Body']
        try:payload=body.read(OBJECT_BYTES+1)
        finally:body.close()
        self._check(payload)
        return dict(response,Body=io.BytesIO(payload))

    def put_object(self,**kwargs):
        key=kwargs['Key']
        if key not in self.writes and len(self.writes)>=ARTIFACTS:raise WorkloadRejected('Artifact count bound')
        self._check(kwargs['Body']);self.writes.add(key)
        return self.client.put_object(**kwargs)


class BoundedExecutor:
    def __init__(self,executor,storage):self.executor,self.storage=executor,storage

    def __call__(self,boundary):
        from app.db import database
        from app.storage.versioned_artifacts import ObjectIdentity
        # Retain existing reentrant dataset/lifecycle/delivery locks while
        # checking current retained state and executing, without changing order.
        with boundary.stage(required_ms=1000) as scope:
            with database.get_connection() as conn:
                row=conn.execute('SELECT identity FROM processing_source_objects WHERE file_id=%s',(scope.file_id,)).fetchone()
                retained,applied=conn.execute('''SELECT coalesce(s.row_count,0),EXISTS(SELECT 1 FROM delivery_applications a
                    WHERE a.upload_request_id=%s AND a.status='SUCCESS' AND a.result_state_id=s.state_id)
                    FROM datasets d LEFT JOIN dataset_state_versions s ON s.state_id=d.current_state_id WHERE d.dataset_id=%s''',
                    (scope.upload_id,scope.dataset_id)).fetchone()
            if row is None:raise WorkloadRejected('Registered source required')
            identity=ObjectIdentity.decode(row[0])
            if identity.size>RAW_BYTES:raise WorkloadRejected('Raw byte bound')
            count=csv_rows(self.storage.read(identity))
            # Conservative worst-case additions include inactive SNAPSHOT rows.
            # Do not reinterpret keys or drop data to fit the resource policy.
            retained_rows(retained,count,scope.operation,already_applied=applied)
            return self.executor(boundary)


def scratch_usage(path):
    """Bounded scan of a caller-owned scratch directory; never deletes files."""
    root=Path(path)
    if not root.is_dir() or root.is_symlink():raise WorkloadRejected('Owned scratch directory required')
    pending=[root];total=0;entries=0
    while pending:
        directory=pending.pop()
        with os.scandir(directory) as iterator:
            for item in iterator:
                entries+=1
                if entries>2048 or item.is_symlink():raise WorkloadRejected('Scratch entry bound')
                if item.is_dir(follow_symlinks=False):pending.append(Path(item.path))
                else:total+=item.stat(follow_symlinks=False).st_size
                if total>SCRATCH_BYTES:raise WorkloadRejected('Scratch byte bound')
    return total
