"""Pinned workspace discovery and explicit auditable review; never executes a join."""
import logging
import time
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import repository_transaction
from app.db import relationship_repository as repository
from app.services.workspace_understanding_service import validate_workspace
from app.services import workspace_understanding_service as workspace_semantics
from app.db import workspace_semantic_repository
from app.services.dataset_profile_service import read_profile
from app.services.dataset_understanding_service import read_understanding
from app.services.dataset_analytics_service import load_pinned_state
from app.storage.incremental_artifacts import IncrementalArtifacts
from app.processing.relationship_evidence import ALGORITHM_VERSION, LIMITS, digest, propose, verify, structure
from app.schemas.relationships import RelationshipsView

logger = logging.getLogger(__name__)

class BoundedArtifacts(IncrementalArtifacts):
    def __init__(self):
        super().__init__(); self.cache = {}; self.bytes_read = 0
    def read(self,key,max_bytes=None):
        if key not in self.cache:
            body = super().read(key,max_bytes=LIMITS['artifact_bytes'])
            self.bytes_read += len(body)
            if self.bytes_read > LIMITS['total_artifact_bytes']: raise ValueError('Artifact memory budget exceeded')
            self.cache[key] = body
        return self.cache[key]

def gather(conn, workspace, user, lock=False):
    c = conn.cursor(row_factory=dict_row)
    w = c.execute('SELECT * FROM workspaces WHERE workspace_id=%s'+(' FOR UPDATE' if lock else ''),(workspace,)).fetchone()
    if not w: raise HTTPException(404,'Workspace not found')
    if w['owner'] != user['owner_key']: raise HTTPException(403,'Workspace access denied')
    rows = c.execute('SELECT * FROM datasets WHERE workspace_id=%s ORDER BY dataset_id'+(' FOR UPDATE' if lock else ''),(workspace,)).fetchall()
    eligible = []; excluded = []; membership = []
    for row in rows:
        membership.append({k:row[k] for k in ('dataset_id','dataset_name','owner','status','current_state_id','current_profile_id','profile_status')})
        reason = ('OWNERSHIP_MISMATCH' if row['owner']!=user['owner_key'] else
                  'ARCHIVED' if row['status']=='ARCHIVED' else 'NO_TRUSTED_STATE' if not row['current_state_id'] else None)
        if not reason:
            p = read_profile(workspace,row['dataset_id'],user)
            if not p['profile_ready']: reason = 'PROFILE_'+p['freshness']
        if reason:
            excluded.append({'dataset_id':row['dataset_id'],'dataset_name':row['dataset_name'] if row['owner']==user['owner_key'] else 'Unavailable dataset','reason':reason})
            continue
        state = c.execute('SELECT * FROM dataset_state_versions WHERE state_id=%s AND dataset_id=%s AND status=\'PUBLISHED\'',(p['current_state_id'],row['dataset_id'])).fetchone()
        if not state: raise RuntimeError('Invalid trusted state context')
        policy = c.execute('SELECT * FROM dataset_load_policies WHERE policy_id=%s AND dataset_id=%s',(state['policy_id'],row['dataset_id'])).fetchone()
        if not policy: raise RuntimeError('Invalid policy context')
        s = read_understanding(workspace,row['dataset_id'],user)
        roles = {x['column_name']:x['suggested_role'] for x in s['suggestion']['reasoning']['columns']} if s['status']=='READY' else {}
        pin = {'dataset_id':row['dataset_id'],'state_id':state['state_id'],'state_version':state['state_version'],
               'dataset_version_id':state['dataset_version_id'],'policy_id':state['policy_id'],
               'profile_id':p['profile_id'],'profile_version':p['profile_version'],
               'schema_version':p['schema_version'],
               'dataset_suggestion_id':s['suggestion']['suggestion_id'] if s['status']=='READY' else None}
        eligible.append({'profile':p,'policy':policy,'state':state,'pin':pin,'semantic_roles':roles})
    ws_source = workspace_semantics.gather(conn,workspace,user)
    ws_run = workspace_semantic_repository.matching(workspace,ws_source['source_signature'],
        workspace_semantics.ALGORITHM_VERSION,workspace_semantics.configuration(workspace_semantics.get_provider()))
    ws_status = ws_run['status'] if ws_run else 'STALE' if workspace_semantic_repository.has_history(workspace) else 'NOT_GENERATED'
    # No stale workspace prose is used. Only its freshness/current source pins strengthen the context.
    context = {'workspace_semantics':ws_status,'workspace_suggestion_id':ws_run['suggestion_id'] if ws_status=='READY' else None,
               'workspace_semantic_source_pins':ws_source['source_pins'] if ws_status=='READY' else [],
               'workspace_domain':ws_run['reasoning']['domain']['primary']['label'] if ws_status=='READY' else None,
               'workspace_entities':[x['canonical_name'] for x in ws_run['reasoning']['entities']] if ws_status=='READY' else [],
               'dataset_semantics_available':sum(bool(x['semantic_roles']) for x in eligible),'ai_ranking':'NOT_USED'}
    coverage = {'datasets_total':len(rows),'datasets_analyzed':len(eligible),'datasets_excluded':len(excluded),
                'partial':bool(excluded),'excluded':excluded}
    signature = digest([membership,[x['pin'] for x in eligible],context,ALGORITHM_VERSION])
    return {'workspace_id':workspace,'eligible':eligible,'coverage':coverage,'semantic_context':context,
            'signature':signature,'pins':[x['pin'] for x in eligible]}

def snapshot(workspace,user):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return gather(conn,workspace,user)

def limitation(source):
    items = source['eligible']
    if len(items)<2: return 'At least two datasets need current READY profiles and trusted state.'
    if len(items)>LIMITS['datasets']: return 'V1 supports at most 20 eligible datasets.'
    if sum(len(x['profile']['columns']) for x in items)>LIMITS['columns']: return 'V1 supports at most 400 columns.'
    if any(x['state']['row_count']>LIMITS['rows_per_dataset'] for x in items): return 'V1 supports at most 100000 trusted rows per dataset.'
    if sum(x['state']['row_count'] for x in items)>LIMITS['total_rows']: return 'V1 supports at most 500000 trusted rows per workspace.'
    return None

def reviewed_view(source, reviews):
    items = {x['pin']['dataset_id']:x for x in source['eligible']}; result = []
    for r in reviews:
        e = r['evidence']; p = items.get(e['parent']['dataset_id']); c = items.get(e['child']['dataset_id'])
        structural = 'UNAVAILABLE'; verification = 'STALE'
        if p and c:
            structural = 'CURRENT' if structure(p,c,e['parent']['columns'][0],e['child']['columns'][0])==e['structural_signature'] else 'REVALIDATION_REQUIRED'
            if e['source_pins']==[p['pin'],c['pin']]: verification = 'CURRENT'
        result.append({'candidate_id':r['candidate_id'],'relationship_version':r['relationship_version'],
            'status':r['status'],'reviewed_by':r['reviewed_by'],'reviewed_at':r['reviewed_at'],'evidence':e,
            'structural_status':structural,'verification_status':verification})
    return result

def read_relationships(workspace,user,content=True):
    source = snapshot(workspace,user)
    with repository_transaction() as conn:
        run = repository.matching(conn,workspace,source['signature'],ALGORITHM_VERSION)
        history = conn.execute('SELECT 1 FROM relationship_discovery_runs WHERE workspace_id=%s LIMIT 1',(workspace,)).fetchone()
        reviews = repository.reviews(conn,workspace)
        records = repository.candidates(conn,run['run_id']) if run and run['status']=='READY' and content else []
    status = run['status'] if run else 'STALE' if history else 'NOT_GENERATED'
    failure = run['failure_code'] if run else None
    if status=='DISCOVERING' and not repository.active(workspace): status,failure = 'FAILED','INTERRUPTED'
    if status=='READY':
        after = snapshot(workspace,user)
        if source['signature']!=after['signature']: source=after;status='STALE';records=[]
    review_map = {r['candidate_key']:r for r in reviews}; candidates = []
    for r in records:
        e = r['evidence']; review = review_map.get(r['candidate_key'])
        current_review = review and review['evidence']['source_pins']==e['source_pins'] and review['evidence']['structural_signature']==e['structural_signature']
        candidates.append({'candidate_id':r['candidate_id'],'run_id':r['run_id'],'candidate_status':'CANDIDATE' if e['can_confirm'] else 'REVIEW_REQUIRED',
            'evidence':e,'review_status':review['status'] if current_review else 'REVIEW_REQUIRED',
            'review_version':review['relationship_version'] if review else 0})
    problem = limitation(source)
    view = {'workspace_id':workspace,'status':status,'run_id':run['run_id'] if run else None,
        'run_version':run['run_version'] if run else None,'failure_code':failure,'can_discover':not problem and status not in ('READY','DISCOVERING'),
        'readiness_message':problem or 'Current profiles are eligible. Dataset/workspace AI context is optional.',
        'coverage':source['coverage'],'semantic_context':source['semantic_context'],'limits':LIMITS,
        'source_pins':source['pins'],'review_revision':max((r['review_id'] for r in reviews),default=0),
        'candidates':candidates,'reviewed_relationships':reviewed_view(source,reviews) if content else []}
    return RelationshipsView.model_validate(view).model_dump()

def load_frames(source,pairs):
    needed = {item['pin']['dataset_id'] for pair in pairs.values() for item in pair[:2]}
    frames = {}; store = BoundedArtifacts()
    with repository_transaction() as conn:
        c = conn.cursor(row_factory=dict_row)
        apps = {x['pin']['dataset_id']:c.execute('SELECT * FROM delivery_applications WHERE application_id=%s',(x['state']['source_application_id'],)).fetchone()
                for x in source['eligible'] if x['pin']['dataset_id'] in needed}
    for item in source['eligible']:
        dataset = item['pin']['dataset_id']
        if dataset not in needed: continue
        _,frame = load_pinned_state(store,item['state'],apps[dataset],item['policy'])
        if item['policy']['load_strategy']=='SNAPSHOT':
            flags = frame['_datarise_active']
            if flags.isna().any() or not flags.map(lambda v:isinstance(v,bool)).all(): raise ValueError('Invalid activity state')
            frame = frame.loc[flags]
        if len(frame)!=item['profile']['summary']['row_count']: raise ValueError('Current profile row count mismatch')
        frames[dataset] = frame
    return frames

def discover(workspace,user):
    validate_workspace(workspace,user); started = time.monotonic()
    with repository.discovery_lock(workspace):
        source = snapshot(workspace,user)
        problem = limitation(source)
        if problem: raise ValueError(problem)
        with repository_transaction() as conn:
            existing = repository.matching(conn,workspace,source['signature'],ALGORITHM_VERSION)
        if existing and existing['status']=='READY': return read_relationships(workspace,user)
        with repository_transaction() as conn:
            current = gather(conn,workspace,user,True)
            if current['signature']!=source['signature']: raise ValueError('Sources changed; reload before discovery')
            c = conn.cursor(row_factory=dict_row)
            if existing:
                run = c.execute("UPDATE relationship_discovery_runs SET status='DISCOVERING',completed_at=NULL,failure_code=NULL WHERE run_id=%s RETURNING *",(existing['run_id'],)).fetchone()
            else:
                version = c.execute('SELECT COALESCE(MAX(run_version),0)+1 AS n FROM relationship_discovery_runs WHERE workspace_id=%s',(workspace,)).fetchone()['n']
                run = c.execute('''INSERT INTO relationship_discovery_runs(workspace_id,owner_key,run_version,algorithm_version,source_signature,source_pins,coverage,semantic_context,status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'DISCOVERING') RETURNING *''',
                    (workspace,user['owner_key'],version,ALGORITHM_VERSION,source['signature'],Jsonb(source['pins']),Jsonb(source['coverage']),Jsonb(source['semantic_context']))).fetchone()
        failure = None
        try:
            pairs,suppressed = propose(source['eligible'])
            evidence = verify(workspace,pairs,load_frames(source,pairs))
            with repository_transaction() as conn:
                current = gather(conn,workspace,user,True)
                if current['signature']!=source['signature']: raise RuntimeError('SOURCE_CHANGED')
                for e in evidence:
                    conn.execute('INSERT INTO relationship_candidates(workspace_id,run_id,candidate_key,evidence) VALUES (%s,%s,%s,%s)',
                                 (workspace,run['run_id'],e['candidate_key'],Jsonb(e)))
                conn.execute("UPDATE relationship_discovery_runs SET status='READY',completed_at=NOW(),candidate_count=%s,suppressed_composite_pairs=%s WHERE run_id=%s",(len(evidence),suppressed,run['run_id']))
        except Exception as exc:
            failure = 'SOURCE_CHANGED' if str(exc)=='SOURCE_CHANGED' else 'LIMIT_EXCEEDED' if isinstance(exc,ValueError) and ('limit' in str(exc).lower() or 'budget' in str(exc).lower()) else 'DISCOVERY_FAILED'
            with repository_transaction() as conn:
                conn.execute("UPDATE relationship_discovery_runs SET status='FAILED',failure_code=%s,completed_at=NOW() WHERE run_id=%s AND status<>'READY'",(failure,run['run_id']))
        logger.info('Relationships workspace=%s run=%s algorithm=%s datasets=%s duration=%.3f status=%s',
                    workspace,run['run_id'],ALGORITHM_VERSION,len(source['eligible']),time.monotonic()-started,failure or 'READY')
        return read_relationships(workspace,user)

def review(workspace,candidate_id,user,status,expected_version):
    validate_workspace(workspace,user)
    with repository_transaction() as conn:
        source = gather(conn,workspace,user,True)
        c = conn.cursor(row_factory=dict_row)
        candidate = c.execute('''SELECT c.*,r.source_signature,r.status AS run_status FROM relationship_candidates c
            JOIN relationship_discovery_runs r ON r.run_id=c.run_id AND r.workspace_id=c.workspace_id
            WHERE c.workspace_id=%s AND c.candidate_id=%s''',(workspace,candidate_id)).fetchone()
        if not candidate: raise HTTPException(404,'Relationship candidate not found in workspace')
        if candidate['run_status']!='READY' or candidate['source_signature']!=source['signature']:
            raise ValueError('Candidate evidence is stale; refresh discovery before review')
        prior = c.execute('SELECT * FROM relationship_reviews WHERE workspace_id=%s AND candidate_key=%s ORDER BY relationship_version DESC LIMIT 1',
                          (workspace,candidate['candidate_key'])).fetchone()
        version = prior['relationship_version'] if prior else 0
        if prior and prior['candidate_id']==candidate_id and prior['status']==status:
            pass
        else:
            if version!=expected_version: raise RuntimeError('Review changed; reload before reviewing')
            if status=='CONFIRMED' and not candidate['evidence']['can_confirm']:
                raise ValueError('Insufficient evidence for confirmation; missing references or unsafe parent key')
            c.execute('''INSERT INTO relationship_reviews(workspace_id,candidate_id,candidate_key,relationship_version,status,reviewed_by)
                VALUES (%s,%s,%s,%s,%s,%s)''',(workspace,candidate_id,candidate['candidate_key'],version+1,status,user['user_id']))
    return read_relationships(workspace,user)
