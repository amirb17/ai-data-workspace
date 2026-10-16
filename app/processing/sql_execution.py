"""Disposable DuckDB worker: registered owned frames only, no external IO, hard kill timeout."""
from datetime import date, datetime
from decimal import Decimal
import json
import math
import multiprocessing
import duckdb
from app.processing.sql_policy import LIMITS
from app.processing.sql_validation import SqlBlocked


def scalar(value):
    if value is None: return None
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53-1: return str(value)
    if isinstance(value, float) and not math.isfinite(value): return None
    if isinstance(value, (str, int, float, bool)): return value
    raise SqlBlocked('TYPE', 'This query returned an unsupported complex type. Select scalar fields.')


def calculate(sql, frames, limits=LIMITS):
    import pyarrow as pa
    config = dict(enable_external_access='false', allow_unsigned_extensions='false',
                  autoinstall_known_extensions='false', autoload_known_extensions='false',
                  threads='1', memory_limit=f'{limits.memory_mb}MB', max_temp_directory_size='0B')
    with duckdb.connect(':memory:', config=config) as engine:
        engine.execute("SET TimeZone='UTC'")
        for alias, frame in frames.items(): engine.register(alias, pa.Table.from_pandas(frame, preserve_index=False))
        engine.execute('SET lock_configuration=true')
        cursor = engine.execute(f'SELECT * FROM ({sql}) AS "__bounded_result" LIMIT {limits.output_rows+1}')
        columns = [dict(name=d[0], type=str(d[1])) for d in cursor.description]
        rows=[]
        result = dict(columns=columns, rows=rows, row_count=0, truncated=False,
                      engine='DuckDB', engine_version=duckdb.__version__)
        used=len(json.dumps(result,allow_nan=False).encode())+len(str(limits.output_rows))-1
        if used>limits.output_bytes:
            raise SqlBlocked('OUTPUT_LIMIT', 'Result exceeds the byte limit. Select fewer or shorter fields.')
        for index in range(limits.output_rows+1):
            record=cursor.fetchone()
            if record is None:break
            if index==limits.output_rows:
                result['truncated']=True;break
            row=[scalar(v) for v in record]
            used+=len(json.dumps(row,allow_nan=False).encode())+(2 if rows else 0)
            if used>limits.output_bytes:
                raise SqlBlocked('OUTPUT_LIMIT', 'Result exceeds the byte limit. Select fewer or shorter fields.')
            rows.append(row)
        result['row_count']=len(rows)
        return result


def _worker(pipe, sql, frames, limits):
    try:
        pipe.send(dict(result=calculate(sql, frames, limits)))
    except SqlBlocked as exc: pipe.send(dict(error=exc.diagnostic()))
    except duckdb.OutOfMemoryException: pipe.send(dict(error=dict(code='MEMORY_LIMIT', message='Query exceeded its memory budget. Simplify the query.')))
    except Exception: pipe.send(dict(error=dict(code='EXECUTION_FAILED', message='The query could not run with the supported SQL dialect. Check expressions and field types.')))
    finally: pipe.close()


def execute(plan, frames, aliases, limits=LIMITS):
    for relation in plan.relationships:
        parent, child = relation['parent'], relation['child']
        pk, fk = parent['columns'][0], child['columns'][0]
        p, c = frames[parent['dataset_id']], frames[child['dataset_id']]
        if p[pk].isna().any() or p[pk].duplicated().any() or c[fk].isna().any() or not c[fk].isin(p[pk]).all():
            raise SqlBlocked('JOIN_DATA_CHANGED', 'The confirmed relationship no longer passes current key/coverage checks.')
        if relation['cardinality']=='ONE_TO_ONE' and c[fk].duplicated().any():
            raise SqlBlocked('JOIN_DATA_CHANGED', 'Current keys no longer support the confirmed one-to-one relationship.')
    if sum(len(f) for f in frames.values())>limits.total_rows or any(len(f)>limits.input_rows for f in frames.values()):
        raise SqlBlocked('INPUT_LIMIT', 'Query sources exceed the V1 row limit.')
    if sum(int(f.memory_usage(deep=True).sum()) for f in frames.values())>limits.input_bytes:
        raise SqlBlocked('INPUT_LIMIT', 'Query sources exceed the V1 materialized input budget.')
    named = {aliases[dataset]: frame for dataset,frame in frames.items()}
    context = multiprocessing.get_context('spawn')
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(writer,plan.sql,named,limits), daemon=True)
    try:
        process.start(); writer.close()
        if not reader.poll(limits.seconds):
            raise SqlBlocked('TIMEOUT', f'Query stopped after {limits.seconds} seconds. No partial result is available.')
        try: payload = reader.recv()
        except EOFError: raise SqlBlocked('EXECUTION_FAILED', 'Query worker stopped without a result.') from None
        if 'error' in payload:
            error=payload['error']; raise SqlBlocked(error['code'],error['message'])
        return payload['result']
    finally:
        if process.pid:
            if process.is_alive(): process.terminate()
            process.join(timeout=5)
            if process.is_alive(): process.kill(); process.join()
        reader.close(); writer.close()
