"""Trusted-state evidence lifecycle; execution can be replaced independently of APIs."""
import logging
import time
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import repository_transaction
from app.db import semantic_profile_repository as repository
from app.services.processing_context_service import validate_scope, lock_scope
from app.services.dataset_analytics_service import load_pinned_state
from app.storage.incremental_artifacts import IncrementalArtifacts
from app.processing.semantic_evidence import ALGORITHM_VERSION,build_evidence

logger=logging.getLogger(__name__)


def readiness(workspace,dataset,user):
    validate_scope(user,workspace,dataset)
    data,run,latest,ready=repository.metadata(dataset,ALGORITHM_VERSION)
    return {'workspace_id':workspace,'dataset_id':dataset,'dataset_name':data['dataset_name'],
        'freshness':data['profile_status'],'profile_ready':ready,'current_state_id':data['current_state_id'],
        'state_version':data['state_version'],'dataset_version_id':data['dataset_version_id'],'schema_version':data['schema_version'],
        'algorithm_version':ALGORITHM_VERSION,'profile_id':run['profile_id'] if run else None,
        'profile_version':run['profile_version'] if run else None,'profile_timestamp':run['completed_at'] if run else None,
        'built_algorithm_version':run['algorithm_version'] if run else None,
        'built_from_state_id':run['source_state_id'] if run else None,'state_updated_at':data['published_at'],
        'can_refresh':data['current_state_id'] is not None and data['profile_status']!='PROFILING',
        'failure_code':latest['failure_code'] if latest else None}


def read_profile(workspace,dataset,user):
    result=readiness(workspace,dataset,user)
    result.update(summary=None,columns=[])
    if result['profile_ready']:
        _,run,_,_=repository.metadata(dataset,ALGORITHM_VERSION)
        if run['profile_id']!=result['profile_id']:
            return {**readiness(workspace,dataset,user),'summary':None,'columns':[]}
        evidence=repository.columns(run['profile_id'])
        after=readiness(workspace,dataset,user)
        if not after['profile_ready'] or after['profile_id']!=run['profile_id']:
            return {**after,'summary':None,'columns':[]}
        return {**after,'summary':run['summary'],'columns':evidence}
    return result


def refresh_profile(workspace,dataset,user):
    validate_scope(user,workspace,dataset)
    started=time.monotonic()
    with repository.profile_lock(workspace,dataset):
        # Trusted DB function owns lock, ownership check and state pin.
        # Public API role does not receive table-write privileges.
        with repository_transaction() as conn:
            c=conn.cursor(row_factory=dict_row)
            head=c.execute('SELECT current_state_id FROM datasets WHERE dataset_id=%s AND workspace_id=%s',
                           (dataset,workspace)).fetchone()
            if not head or not head['current_state_id']:
                raise ValueError('Trusted dataset update required before profiling')
            profile_id,reused=conn.execute(
                'SELECT profile_id,reused FROM task22_profile_begin(%s,%s,%s,%s,%s)',
                (user['user_id'],workspace,dataset,head['current_state_id'],ALGORITHM_VERSION)
            ).fetchone()
            run=c.execute('SELECT * FROM semantic_profiles WHERE profile_id=%s',(profile_id,)).fetchone()
            state=c.execute("SELECT * FROM dataset_state_versions WHERE state_id=%s AND dataset_id=%s AND status='PUBLISHED'",
                            (run['source_state_id'],dataset)).fetchone()
            application=c.execute('SELECT * FROM delivery_applications WHERE application_id=%s',
                                  (state['source_application_id'],)).fetchone()
            policy=c.execute('SELECT * FROM dataset_load_policies WHERE policy_id=%s',
                             (state['policy_id'],)).fetchone()
            rules=c.execute('SELECT rules FROM approved_rule_policies WHERE dataset_version_id=%s AND rule_version=%s',
                            (state['dataset_version_id'],application['applied_rule_version'])).fetchone()
        if reused: return read_profile(workspace,dataset,user)
        try:
            logger.info('Profile workspace=%s dataset=%s state=%s profile=%s version=%s algorithm=%s status=PROFILING',workspace,dataset,state['state_version'],run['profile_id'],run['profile_version'],ALGORITHM_VERSION)
            _,frame=load_pinned_state(IncrementalArtifacts(),state,application,policy)
            required=[r['column_name'] for r in rules['rules'] if r['rule_type']=='NOT_NULL' and r['rule_config'].get('required') is True] if rules else []
            summary,evidence=build_evidence(frame,policy,required)
            summary.update(rule_version=application['applied_rule_version'],snapshot_effective_at=application['snapshot_effective_at'].isoformat() if application.get('snapshot_effective_at') else None)
            with repository_transaction() as conn:
                conn.execute('SELECT task22_profile_publish(%s,%s,%s,%s,%s,%s,%s)',
                             (user['user_id'],workspace,dataset,run['profile_id'],
                              state['state_id'],Jsonb(summary),Jsonb(evidence)))
        except Exception:
            with repository_transaction() as conn:
                conn.execute('SELECT task22_profile_fail(%s,%s,%s,%s,%s)',
                             (user['user_id'],workspace,dataset,run['profile_id'],state['state_id']))
            logger.warning('Profile workspace=%s dataset=%s state=%s profile=%s algorithm=%s status=FAILED category=PROFILE_FAILED',workspace,dataset,state['state_version'],run['profile_id'],ALGORITHM_VERSION)
            raise RuntimeError('Profile failed; previous evidence and trusted data retained') from None
        logger.info('Profile workspace=%s dataset=%s state=%s profile=%s version=%s algorithm=%s rows=%s columns=%s duration=%.3f status=READY',workspace,dataset,state['state_version'],run['profile_id'],run['profile_version'],ALGORITHM_VERSION,summary['row_count'],summary['column_count'],time.monotonic()-started)
        return read_profile(workspace,dataset,user)
