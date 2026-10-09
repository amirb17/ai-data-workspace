"""Explicit workspace reasoning over current dataset facts and unapproved suggestions."""
import hashlib
import json
import logging
import time
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import repository_transaction
from app.db import workspace_semantic_repository as repository
from app.db.workspace_repository import get_workspace_by_id
from app.services.dataset_profile_service import read_profile
from app.services.dataset_understanding_service import read_understanding
from app.ai.workspace_semantics import WorkspaceProvider, ALGORITHM_VERSION, build_handoff, validate_output
from app.ai.semantic_provider import ProviderFailure
from app.schemas.workspace_semantics import WorkspaceSuggestion, WorkspaceSemanticEvidenceBundle

logger=logging.getLogger(__name__)

def get_provider():return WorkspaceProvider()

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()

def configuration(provider):return digest([provider.provider,provider.model,provider.strategy,ALGORITHM_VERSION])

def validate_workspace(workspace,user):
    row=get_workspace_by_id(workspace)
    if row is None:raise HTTPException(404,'Workspace not found')
    if row[3]!=user['owner_key']:raise HTTPException(403,'Workspace access denied')
    return row

def gather(conn,workspace,user,lock=False):
    """Caller owns transaction. Publication locks membership parent then all child heads.

    Parent FOR UPDATE conflicts with FK key-share on new/moved memberships. Ordered child
    locks serialize trusted-head changes, archive, removal, and dataset suggestion insertion.
    Snapshot reads use REPEATABLE READ; locked publication uses READ COMMITTED after waits.
    """
    c=conn.cursor(row_factory=dict_row)
    w=c.execute('SELECT * FROM workspaces WHERE workspace_id=%s'+(' FOR UPDATE' if lock else ''),(workspace,)).fetchone()
    if not w:raise HTTPException(404,'Workspace not found')
    if w['owner']!=user['owner_key']:raise HTTPException(403,'Workspace access denied')
    rows=c.execute('SELECT * FROM datasets WHERE workspace_id=%s ORDER BY dataset_id'+(' FOR UPDATE' if lock else ''),(workspace,)).fetchall()
    eligible,excluded,membership=[],[],[]
    for row in rows:
        membership.append({k:row[k] for k in ('dataset_id','dataset_name','owner','status','current_state_id','current_profile_id','profile_status')})
        reason=None
        if row['status']=='ARCHIVED':reason='ARCHIVED'
        elif row['owner']!=user['owner_key']:reason='OWNERSHIP_MISMATCH'
        elif not row['current_state_id']:reason='NO_TRUSTED_STATE'
        if not reason:
            p=read_profile(workspace,row['dataset_id'],user)
            if not p['profile_ready']:reason='PROFILE_'+p['freshness']
        if not reason:
            s=read_understanding(workspace,row['dataset_id'],user)
            if s['status']!='READY':reason='DATASET_UNDERSTANDING_'+s['status']
        if reason:
            excluded.append({'dataset_id':row['dataset_id'],
                'dataset_name':row['dataset_name'] if row['owner']==user['owner_key'] else 'Unavailable dataset','reason':reason})
        else:
            record=s['suggestion']
            pin={'dataset_id':row['dataset_id'],'dataset_name':row['dataset_name'],'profile_id':p['profile_id'],
                'profile_version':p['profile_version'],'state_id':p['current_state_id'],'state_version':p['state_version'],
                'dataset_version_id':p['dataset_version_id'],'schema_version':p['schema_version'],
                'suggestion_id':record['suggestion_id'],'suggestion_version':record['suggestion_version']}
            eligible.append({'profile':p,'suggestion':record,'pin':pin})
    coverage={'datasets_total':len(rows),'datasets_analyzed':len(eligible),'datasets_excluded':len(excluded),
        'partial':len(excluded)>0,'excluded':excluded}
    signature=digest({'workspace_name':w['workspace_name'],'membership':membership,'coverage':coverage,
        'pins':[x['pin'] for x in eligible]})
    return {'workspace_id':workspace,'workspace_name':w['workspace_name'],'coverage':coverage,
        'eligible':eligible,'source_signature':signature,'source_pins':[x['pin'] for x in eligible]}

def snapshot(workspace,user):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return gather(conn,workspace,user)

def response(source,run,status,failure=None,include_content=True):
    count=source['coverage']['datasets_analyzed']
    exceeds_limit=count>50 or sum(len(x['profile']['columns']) for x in source['eligible'])>1000
    message=('Refresh dataset profiles and AI understanding; no eligible datasets.' if not count else
        'Direct analysis supports at most 50 eligible datasets and 1000 columns. Reduce the workspace size.' if exceeds_limit else
        'Partial coverage: excluded datasets need review.' if source['coverage']['partial'] else 'All datasets are eligible.')
    return {'workspace_id':source['workspace_id'],'workspace_name':source['workspace_name'],
        'status':status,'failure_code':failure,'coverage':source['coverage'],'source_pins':source['source_pins'],
        'can_generate':count>0 and not exceeds_limit and status not in ('READY','GENERATING'), 'readiness_message':message,
        'suggestion':WorkspaceSuggestion.model_validate(run).model_dump() if status=='READY' and include_content else None}

def read_understanding_workspace(workspace,user,include_content=True):
    source=snapshot(workspace,user)
    provider=get_provider()
    run=repository.matching(workspace,source['source_signature'],ALGORITHM_VERSION,configuration(provider))
    if run and (run['source_pins']!=source['source_pins'] or run['coverage']!=source['coverage']):
        raise RuntimeError('Stored workspace source context mismatch')
    status='STALE' if repository.has_history(workspace) else 'NOT_GENERATED'
    failure=None
    if run and source['eligible']:
        status,failure=run['status'],run['failure_code']
        if status=='GENERATING' and not repository.active(workspace):status,failure='FAILED','INTERRUPTED'
    if status=='READY':
        after=snapshot(workspace,user)
        if after['source_signature']!=source['source_signature']:return response(after,None,'STALE',include_content=False)
    return response(source,run,status,failure,include_content)

def generate_workspace(workspace,user):
    validate_workspace(workspace,user)
    started=time.monotonic()
    with repository.generation_lock(workspace):
        source=snapshot(workspace,user)
        if not source['eligible']:raise ValueError('No eligible datasets; refresh dataset profiles and AI understanding')
        provider=get_provider();config=configuration(provider)
        existing=repository.matching(workspace,source['source_signature'],ALGORITHM_VERSION,config)
        if existing and (existing['source_pins']!=source['source_pins'] or existing['coverage']!=source['coverage']):
            raise RuntimeError('Stored workspace source context mismatch')
        if existing and existing['status']=='READY':return read_understanding_workspace(workspace,user)
        bundle,encoded,columns=build_handoff(source)
        with repository_transaction() as conn:
            current=gather(conn,workspace,user,lock=True)
            if current['source_signature']!=source['source_signature']:raise ValueError('Sources changed; reload before analysis')
            c=conn.cursor(row_factory=dict_row)
            if existing:
                run=c.execute("UPDATE workspace_semantic_suggestions SET status='GENERATING',completed_at=NULL,failure_code=NULL WHERE suggestion_id=%s RETURNING *",(existing['suggestion_id'],)).fetchone()
            else:
                version=c.execute('SELECT COALESCE(MAX(suggestion_version),0)+1 AS n FROM workspace_semantic_suggestions WHERE workspace_id=%s',(workspace,)).fetchone()['n']
                run=c.execute('''INSERT INTO workspace_semantic_suggestions(workspace_id,owner_key,suggestion_version,algorithm_version,
                    source_signature,configuration_key,source_pins,coverage,provider,model,status,compact_mode,input_bytes,column_count)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'GENERATING',%s,%s,%s) RETURNING *''',
                    (workspace,user['owner_key'],version,ALGORITHM_VERSION,source['source_signature'],config,
                    Jsonb(source['source_pins']),Jsonb(source['coverage']),provider.provider,provider.model,bundle['compact_mode'],len(encoded.encode()),columns)).fetchone()
        failure=None
        try:
            output=provider.generate(encoded)
            try:reasoning=validate_output(output.text,source)
            except (ValueError,TypeError):raise ProviderFailure('INVALID_OUTPUT') from None
            with repository_transaction() as conn:
                current=gather(conn,workspace,user,lock=True)
                if current['source_signature']!=source['source_signature'] or configuration(get_provider())!=config:
                    raise ProviderFailure('GENERATION_FAILED')
                conn.execute("UPDATE workspace_semantic_suggestions SET status='READY',reasoning=%s,resolved_model=%s,completed_at=NOW() WHERE suggestion_id=%s",
                    (Jsonb(reasoning.model_dump()),output.resolved_model,run['suggestion_id']))
        except Exception as exc:
            failure=exc.code if isinstance(exc,ProviderFailure) else 'GENERATION_FAILED'
            with repository_transaction() as conn:
                conn.execute("UPDATE workspace_semantic_suggestions SET status='FAILED',failure_code=%s,completed_at=NOW() WHERE suggestion_id=%s AND status<>'READY'",(failure,run['suggestion_id']))
        logger.info('Workspace understanding workspace=%s suggestion=%s algorithm=%s provider=%s model=%s total=%s analyzed=%s source_profile_count=%s source_suggestion_count=%s columns=%s compact=%s input_bytes=%s duration=%.3f status=%s validation=%s',
            workspace,run['suggestion_id'],ALGORITHM_VERSION,provider.provider,provider.model,
            source['coverage']['datasets_total'],len(source['eligible']),len(source['source_pins']),len(source['source_pins']),columns,bundle['compact_mode'],len(encoded.encode()),
            time.monotonic()-started,'FAILED' if failure else 'READY',failure or 'VALID')
        return read_understanding_workspace(workspace,user)

def evidence_bundle(workspace,user):
    source=snapshot(workspace,user)
    current=read_understanding_workspace(workspace,user)
    if current['status']!='READY' or current['source_pins']!=source['source_pins']:
        raise ValueError('Current workspace understanding required')
    after=snapshot(workspace,user)
    if after['source_signature']!=source['source_signature']:raise ValueError('Sources changed')
    return WorkspaceSemanticEvidenceBundle(workspace_id=workspace,coverage=source['coverage'],
        profiles=[x['profile'] for x in source['eligible']],dataset_suggestions=[x['suggestion'] for x in source['eligible']],
        workspace_suggestion=current['suggestion'],dataset_roles=current['suggestion']['reasoning']['dataset_roles'],
        entity_inventory=current['suggestion']['reasoning']['entities'])
