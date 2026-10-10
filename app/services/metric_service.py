"""Definition discovery only. No raw data, SQL generation, execution or analytics mutation."""
import logging
import time
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import repository_transaction
from app.db import metric_repository as repository, relationship_repository, workspace_semantic_repository
from app.services import workspace_understanding_service as workspace_semantics, relationship_service
from app.ai.metric_discovery import MetricProvider, handoff, output
from app.ai.semantic_provider import ProviderFailure
from app.processing.metric_validator import validate_metric, reconcile, ALGORITHM_VERSION, POLICY_VERSION
from app.processing.relationship_evidence import digest

logger=logging.getLogger(__name__)
def get_provider():return MetricProvider()
def configuration(p):return digest([p.provider,p.model,p.strategy,ALGORITHM_VERSION,POLICY_VERSION])

def gather(conn,workspace,user,lock=False):
    s=workspace_semantics.gather(conn,workspace,user,lock)
    wr=workspace_semantic_repository.matching(workspace,s['source_signature'],workspace_semantics.ALGORITHM_VERSION,workspace_semantics.configuration(workspace_semantics.get_provider()))
    relsource=relationship_service.gather(conn,workspace,user)
    reviews=relationship_repository.reviews(conn,workspace)
    ids={x['pin']['dataset_id'] for x in s['eligible']}
    reviewed=relationship_service.reviewed_view(relsource,reviews)
    relations=[r for r in reviewed if r['status']=='CONFIRMED' and r['structural_status']=='CURRENT' and r['verification_status']=='CURRENT' and {p['dataset_id'] for p in r['evidence']['source_pins']}<=ids]
    c=conn.cursor(row_factory=dict_row)
    members=c.execute('''SELECT d.dataset_id,d.status,d.current_state_id,s.dataset_version_id,s.policy_id,
        p.schema_columns,p.business_keys,p.normalization_version,d.current_gold_run_id,d.state_analytics_status,
        g.source_state_id AS gold_state_id,g.status AS gold_status FROM datasets d
        LEFT JOIN dataset_state_versions s ON s.state_id=d.current_state_id
        LEFT JOIN dataset_load_policies p ON p.policy_id=s.policy_id
        LEFT JOIN dataset_gold_runs g ON g.gold_run_id=d.current_gold_run_id WHERE d.workspace_id=%s AND d.owner=%s''',(workspace,user['owner_key'])).fetchall()
    def structural(row):return digest([row['dataset_version_id'],row['policy_id'],row['schema_columns'],row['business_keys'],row['normalization_version']])
    membermap={r['dataset_id']:{**r,'structural_key':structural(r)} for r in members}
    for item in s['eligible']:
        item['pin']={**item['pin'],'structural_key':membermap[item['pin']['dataset_id']]['structural_key'],
            'semantic_key':digest(item['suggestion']['reasoning'])}
    source={**s,'source_pins':[x['pin'] for x in s['eligible']], 'members':membermap,
        'workspace_suggestion_id':wr['suggestion_id'] if wr and wr['status']=='READY' else None,
        'workspace_reasoning':wr['reasoning'] if wr and wr['status']=='READY' else None,'relationships':relations,
        'relationship_reviews':{r['candidate_key']:r for r in reviews},'reviewed_relationships':reviewed}
    source['signature']=digest([s['source_signature'],source['source_pins'],source['workspace_suggestion_id'],
        [(r['candidate_key'],r['relationship_version'],r['status']) for r in reviews],ALGORITHM_VERSION,POLICY_VERSION])
    return source

def snapshot(workspace,user):
    workspace_semantics.validate_workspace(workspace,user)
    with repository_transaction() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return gather(conn,workspace,user)

def freshness(e,source):
    definition='CURRENT';dependencies_current=True;data_current=True
    eligible={x['pin']['dataset_id']:x for x in source['eligible']}
    current_rel={r['candidate_id']:r for r in source['relationships']}
    for d in e['dependencies']:
        if d['kind']=='DATASET':
            row=source['members'].get(d['dataset_id']);item=eligible.get(d['dataset_id'])
            if not row or row['status']=='ARCHIVED':definition='UNAVAILABLE'
            elif row['structural_key']!=d['structural_key'] or (item and item['pin']['semantic_key']!=d['semantic_key']):
                if definition!='UNAVAILABLE':definition='REVALIDATION_REQUIRED'
            dependencies_current &= bool(item and item['pin']=={k:v for k,v in d.items() if k!='kind'})
            data_current &= bool(row and row['status']!='ARCHIVED' and row['gold_status']=='SUCCESS' and row['gold_state_id']==row['current_state_id'] and row['state_analytics_status']=='FRESH')
        elif d['kind']=='WORKSPACE':dependencies_current &= d['suggestion_id']==source['workspace_suggestion_id']
        else:
            r=current_rel.get(d['candidate_id'])
            dependencies_current &= bool(r and r['relationship_version']==d['relationship_version'])
            if not r:
                # Keep prior definition visible, but never allow stale/rejected joins as usable.
                data_current=False
            reviewed=next((r for r in source.get('reviewed_relationships',[]) if r['candidate_id']==d['candidate_id']),None)
            if (not reviewed or reviewed['status']!='CONFIRMED' or reviewed['structural_status']!='CURRENT' or reviewed['relationship_version']!=d['relationship_version']) and definition!='UNAVAILABLE':
                definition='REVALIDATION_REQUIRED'
    return {'definition_freshness':definition,'dependency_freshness':'CURRENT' if dependencies_current else 'STALE',
        'data_freshness':'CURRENT' if data_current else 'REFRESH_REQUIRED'}

def read_metrics(workspace,user,content=True):
    source=snapshot(workspace,user);config=configuration(get_provider())
    with repository_transaction() as conn:
        c=conn.cursor(row_factory=dict_row);run=repository.matching(conn,workspace,source['signature'],config)
        last=c.execute("SELECT * FROM metric_discovery_runs WHERE workspace_id=%s AND status='READY' ORDER BY run_version DESC LIMIT 1",(workspace,)).fetchone()
        history=c.execute('SELECT 1 FROM metric_discovery_runs WHERE workspace_id=%s LIMIT 1',(workspace,)).fetchone()
        show=run if run and run['status']=='READY' else last
        records=c.execute('SELECT * FROM metric_candidates WHERE run_id=%s ORDER BY candidate_id',(show['run_id'],)).fetchall() if show and content else []
        reviews=repository.latest_reviews(conn,workspace);rm={r['candidate_id']:r for r in reviews}
        approvals=c.execute('SELECT candidate_id,metric_id,approval_kind FROM approved_metric_definitions WHERE workspace_id=%s',(workspace,)).fetchall()
        am={a['candidate_id']:a for a in approvals}
    # Avoid returning a late response for a new source combination.
    after=snapshot(workspace,user)
    if after['signature']!=source['signature']:source=after;run=None
    status=run['status'] if run else 'STALE' if history else 'NOT_GENERATED'
    failure=run['failure_code'] if run else None
    if status=='GENERATING' and not repository.active(workspace):status,failure='FAILED','INTERRUPTED'
    candidates=[]
    for row in records:
        e=row['evidence'];f=freshness(e,source);r=rm.get(row['candidate_id']);a=am.get(row['candidate_id'])
        current=bool(run and show and run['run_id']==show['run_id'] and status=='READY' and f['dependency_freshness']=='CURRENT' and f['definition_freshness']=='CURRENT')
        review_status=r['status'] if r else 'AUTO_ACCEPTED' if a else 'UNREVIEWED'
        candidates.append({'workspace_id':workspace,'candidate_id':row['candidate_id'],'run_id':row['run_id'],**e,**f,
            'review_status':review_status,'review_version':r['review_version'] if r else 0,
            'reviewed_at':r['reviewed_at'] if r else None,'reviewer':r['reviewer'] if r else None,
            'metric_id':a['metric_id'] if a else None,'approval_kind':a['approval_kind'] if a else None,
            'can_approve':current and e['validation_status']=='VALID' and review_status not in ('APPROVED','AUTO_ACCEPTED'),
            'can_reject':current and review_status!='REJECTED','recommendation_usable':current and e['validation_status']=='VALID' and review_status!='REJECTED',
            'execution_available':False,'preview_value':None})
    can=bool(source['eligible'] and source['workspace_reasoning'])
    within=len(source['eligible'])<=20 and sum(len(x['profile']['columns']) for x in source['eligible'])<=400
    return {'workspace_id':workspace,'status':status,'run_id':run['run_id'] if run else None,'run_version':run['run_version'] if run else None,
        'failure_code':failure,'source_pins':source['source_pins'],'workspace_suggestion_id':source['workspace_suggestion_id'],
        'review_revision':max((r['review_id'] for r in reviews),default=0),
        # Gold freshness is a UI dependency, not a reason to charge for a new AI run.
        'freshness_revision':digest([source['members'],source['relationship_reviews']]),'coverage':source['coverage'],
        'can_discover':can and within and status not in ('READY','GENERATING'),
        'readiness_message':'Current workspace/dataset understanding required.' if not can else 'V1 supports 20 datasets and 400 columns.' if not within else 'Definitions only; no metric query or dashboard runs.',
        'candidates':candidates,'policy_version':POLICY_VERSION,
        'discovery_context':{k:show[k] for k in ('run_id','run_version','algorithm_version','policy_version','provider','model','resolved_model','workspace_suggestion_id')} if show else None}

def discover_metrics(workspace,user):
    workspace_semantics.validate_workspace(workspace,user);started=time.monotonic()
    with repository.discovery_lock(workspace):
        source=snapshot(workspace,user);provider=get_provider();config=configuration(provider)
        encoded=handoff(source)
        with repository_transaction() as conn:existing=repository.matching(conn,workspace,source['signature'],config)
        if existing and existing['status']=='READY':return read_metrics(workspace,user)
        with repository_transaction() as conn:
            current=gather(conn,workspace,user,True)
            if current['signature']!=source['signature']:raise ValueError('Metric sources changed')
            c=conn.cursor(row_factory=dict_row)
            if existing:run=c.execute("UPDATE metric_discovery_runs SET status='GENERATING',failure_code=NULL,completed_at=NULL WHERE run_id=%s RETURNING *",(existing['run_id'],)).fetchone()
            else:
                version=c.execute('SELECT COALESCE(MAX(run_version),0)+1 AS n FROM metric_discovery_runs WHERE workspace_id=%s',(workspace,)).fetchone()['n']
                run=c.execute('''INSERT INTO metric_discovery_runs(workspace_id,owner_key,run_version,algorithm_version,policy_version,source_signature,configuration_key,
                    source_pins,workspace_suggestion_id,provider,model,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'GENERATING') RETURNING *''',
                    (workspace,user['owner_key'],version,ALGORITHM_VERSION,POLICY_VERSION,source['signature'],config,Jsonb(source['source_pins']),source['workspace_suggestion_id'],provider.provider,provider.model)).fetchone()
        failure=None
        try:
            response=provider.generate(encoded)
            try:proposals=output(response.text);evidence=[validate_metric(m.model_dump(),source) for m in proposals.metrics]
            except (ValueError,TypeError,KeyError):raise ProviderFailure('INVALID_OUTPUT') from None
            evidence=reconcile(evidence)
            with repository_transaction() as conn:
                current=gather(conn,workspace,user,True)
                if current['signature']!=source['signature'] or configuration(get_provider())!=config:raise ProviderFailure('SOURCE_CHANGED')
                for e in evidence:
                    cid=conn.execute('INSERT INTO metric_candidates(workspace_id,run_id,formula_key,evidence) VALUES (%s,%s,%s,%s) RETURNING candidate_id',(workspace,run['run_id'],e['formula_key'],Jsonb(e))).fetchone()[0]
                    for ordinal,d in enumerate(e['dependencies']):conn.execute('INSERT INTO metric_candidate_dependencies(candidate_id,ordinal,dependency) VALUES (%s,%s,%s)',(cid,ordinal,Jsonb(d)))
                    if e['decision']=='AUTO_ACCEPT':
                        conn.execute("INSERT INTO approved_metric_definitions(workspace_id,candidate_id,approval_kind,definition,dependencies,policy_version) VALUES (%s,%s,'SYSTEM_POLICY',%s,%s,%s)",(workspace,cid,Jsonb(e['definition']),Jsonb(e['dependencies']),POLICY_VERSION))
                conn.execute("UPDATE metric_discovery_runs SET status='READY',completed_at=NOW(),resolved_model=%s WHERE run_id=%s",(response.resolved_model,run['run_id']))
        except Exception as exc:
            failure=exc.code if isinstance(exc,ProviderFailure) else 'GENERATION_FAILED'
            with repository_transaction() as conn:conn.execute("UPDATE metric_discovery_runs SET status='FAILED',failure_code=%s,completed_at=NOW() WHERE run_id=%s AND status<>'READY'",(failure,run['run_id']))
        logger.info('Metrics workspace=%s run=%s policy=%s bytes=%s duration=%.3f status=%s',workspace,run['run_id'],POLICY_VERSION,len(encoded.encode()),time.monotonic()-started,failure or 'READY')
        return read_metrics(workspace,user)

def review_metric(workspace,candidate,user,status,expected):
    workspace_semantics.validate_workspace(workspace,user)
    with repository_transaction() as conn:
        source=gather(conn,workspace,user,True);c=conn.cursor(row_factory=dict_row)
        row=c.execute('''SELECT c.*,r.source_signature,r.configuration_key,r.status AS run_status FROM metric_candidates c
            JOIN metric_discovery_runs r USING(workspace_id,run_id) WHERE c.workspace_id=%s AND c.candidate_id=%s''',(workspace,candidate)).fetchone()
        if not row:raise HTTPException(404,'Metric candidate not found in workspace')
        if row['run_status']!='READY' or row['source_signature']!=source['signature'] or row['configuration_key']!=configuration(get_provider()):raise ValueError('Metric dependencies changed; refresh discovery')
        if status=='APPROVED' and row['evidence']['validation_status']!='VALID':raise ValueError('Unresolved/invalid metric cannot be approved')
        prior=c.execute('SELECT * FROM metric_reviews WHERE candidate_id=%s ORDER BY review_version DESC LIMIT 1',(candidate,)).fetchone()
        if not prior or prior['status']!=status:
            version=prior['review_version'] if prior else 0
            if version!=expected:raise RuntimeError('Metric review changed; reload')
            r=c.execute('INSERT INTO metric_reviews(workspace_id,candidate_id,review_version,status,reviewer) VALUES (%s,%s,%s,%s,%s) RETURNING *',(workspace,candidate,version+1,status,user['user_id'])).fetchone()
            if status=='APPROVED':
                e=row['evidence'];conn.execute("INSERT INTO approved_metric_definitions(workspace_id,candidate_id,approval_kind,review_id,definition,dependencies,policy_version) VALUES (%s,%s,'USER_REVIEW',%s,%s,%s,%s) ON CONFLICT(candidate_id) DO NOTHING",(workspace,candidate,r['review_id'],Jsonb(e['definition']),Jsonb(e['dependencies']),POLICY_VERSION))
    return read_metrics(workspace,user)
