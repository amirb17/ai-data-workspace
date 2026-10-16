"""Separate advanced SQL surface; no Ask, metric registry or source mutation."""
from types import SimpleNamespace
import time
import logging
import duckdb
from threading import BoundedSemaphore
from fastapi import HTTPException
from dataclasses import asdict
from app.db.database import repository_transaction
from app.db import sql_repository as repository
from app.services import sql_catalog_service as catalog_service
from app.services import workspace_analytics_service as gold
from app.processing.metric_execution import BoundedMetricArtifacts
from app.processing.sql_inputs import guard_inputs
from app.processing.sql_policy import LIMITS
from app.processing.sql_validation import validate, format_sql, SqlBlocked
from app.processing.sql_execution import execute as run_engine
from app.processing.relationship_evidence import digest

logger=logging.getLogger(__name__)
slots=BoundedSemaphore(LIMITS.concurrency)


def validate_query(workspace,user,request):
    catalog=catalog_service.snapshot(workspace,user)
    try:
        plan=validate(request.sql_text,catalog)
        return dict(workspace_id=workspace,status='VALID',normalized_sql=plan.sql,
                    formatted_sql=format_sql(request.sql_text),warnings=plan.warnings,diagnostics=[])
    except SqlBlocked as exc:
        return dict(workspace_id=workspace,status='REFRESH_REQUIRED' if exc.code=='REFRESH_REQUIRED' else 'INVALID',diagnostics=[exc.diagnostic()])


def execute_query(workspace,user,request):
    if not slots.acquire(blocking=False):
        raise HTTPException(429,'SQL execution capacity is busy. Retry after another query completes.')
    try: return _execute_query(workspace,user,request)
    finally: slots.release()


def _execute_query(workspace,user,request):
    started=time.monotonic(); catalog=catalog_service.snapshot(workspace,user)
    if request.saved_query_id:
        with repository_transaction() as conn: repository.owned_query(conn,workspace,user,request.saved_query_id)
    result={}; dependencies=[]; relations=[]; signature=None; normalized=None
    status='BLOCKED'; diagnostics=[]
    try:
        plan=validate(request.sql_text,catalog); normalized=plan.sql
        dependencies=catalog_service.pins(catalog,plan.dataset_ids); relations=list(plan.relationships)
        signature=catalog_service.signature(catalog,plan.dataset_ids,relations)
        adapter=SimpleNamespace(workspace_id=workspace,sources=dependencies)
        with repository_transaction() as conn: handles=gold.load_sources(conn,adapter)
        if len(handles)>LIMITS.datasets or sum(base['row_count'] for _,_,base in handles)>LIMITS.total_rows or any(base['row_count']>LIMITS.input_rows for _,_,base in handles):
            raise SqlBlocked('INPUT_LIMIT','Query source rows exceed the input budget.')
        # Existing immutable checksummed cumulative BASE reader, additionally bounded in aggregate.
        store=guard_inputs(BoundedMetricArtifacts.create(),LIMITS)
        frames=gold.read_frames(handles,store)
        selected={d['dataset_id']:d for d in catalog['datasets'] if d['dataset_id'] in plan.dataset_ids}
        frames={key:frame[[c['name'] for c in selected[key]['columns'] if c['queryable']]] for key,frame in frames.items()}
        result=run_engine(plan,frames,{key:d['alias'] for key,d in selected.items()})
        after=catalog_service.snapshot(workspace,user)
        if catalog_service.signature(after,plan.dataset_ids,[r for r in after['relationships'] if r in relations])!=signature:
            raise SqlBlocked('SOURCE_CHANGED','Sources or relationship verification changed during execution. Run again with current data.')
        status='SUCCEEDED'
    except SqlBlocked as exc:
        diagnostics=[exc.diagnostic()]; result={}; status='TIMED_OUT' if exc.code=='TIMEOUT' else 'BLOCKED'
    except MemoryError:
        diagnostics=[dict(code='MEMORY_LIMIT',message='Query input loading exceeded available memory. No result is available.')]
        result={}; status='FAILED'
    except Exception:
        diagnostics=[dict(code='EXECUTION_FAILED',message='Trusted query inputs or execution are unavailable. Refresh sources and retry.')]
        result={}; status='FAILED'
    duration=int((time.monotonic()-started)*1000)
    payload=dict(status=status,duration_ms=duration,diagnostics=diagnostics,sources=dependencies,
        relationships=relations,source_signature=signature,normalized_sql=normalized,
        configuration=asdict(LIMITS),trust_label='Ad hoc SQL result',**{k:v for k,v in result.items() if k!='rows'})
    def check_current(conn):
        nonlocal status, result
        if status != 'SUCCEEDED': return
        current=catalog_service.gather(conn,workspace,user,lock=True)
        ids=[d['dataset_id'] for d in dependencies]
        current_relations=[r for r in current['relationships'] if r in relations]
        if catalog_service.signature(current,ids,current_relations)!=signature:
            status='BLOCKED'; result={}
            payload.update(status=status,diagnostics=[SqlBlocked('SOURCE_CHANGED','Sources changed before result publication. Run again with current data.').diagnostic()])
            for key in ('columns','row_count','truncated','engine','engine_version'): payload.pop(key,None)
    identity=repository.record(workspace,user,request.sql_text,digest([normalized or request.sql_text,dependencies,relations,asdict(LIMITS),duckdb.__version__,1]),payload,request.saved_query_id,check_current)
    logger.info('SQL execution id=%s workspace=%s principal=%s status=%s duration_ms=%s sources=%s rows=%s truncated=%s',
        identity['execution_id'],workspace,user['user_id'],status,duration,[d['dataset_id'] for d in dependencies],result.get('row_count'),result.get('truncated'))
    return dict(workspace_id=workspace,**identity,**payload,rows=result.get('rows',[]),freshness='Current' if status=='SUCCEEDED' else 'Unavailable')


def history(workspace,user,execution_id=None):
    catalog=catalog_service.snapshot(workspace,user); output=[]
    for row in repository.history(workspace,user,execution_id):
        p=row['payload']; ids=[d['dataset_id'] for d in p['sources']]
        relations=[r for r in catalog['relationships'] if r in p['relationships']]
        current=p['status']=='SUCCEEDED' and catalog_service.signature(catalog,ids,relations)==p['source_signature']
        output.append(dict(workspace_id=workspace,execution_id=row['execution_id'],saved_query_id=row['saved_query_id'],
            sql_text=row['sql_text'],executed_at=row['created_at'],**p,
            freshness='Current' if current else 'Data changed' if p['status']=='SUCCEEDED' else 'Unavailable',
            rows=[],historical=True))
    return output[0] if execution_id else output
