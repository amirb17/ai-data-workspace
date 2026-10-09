"""Cumulative analytics refresh independently of ingestion and state application."""
from contextlib import contextmanager
import logging
import time
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import get_connection, repository_transaction
from app.services.processing_context_service import validate_scope
from app.processing.cumulative_gold import build_cumulative_gold
from app.storage.cumulative_gold_artifacts import CumulativeGoldArtifacts

logger = logging.getLogger(__name__)


@contextmanager
def refresh_lock(workspace_id, dataset_id):
    # Separate namespace: a delivery may publish during the build; publication uses CAS.
    with get_connection() as conn:
        conn.autocommit = True
        namespace = conn.execute('SELECT current_schema()').fetchone()[0]
        key = f'{namespace}:datarise:analytics:{workspace_id}:{dataset_id}'
        if not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', (key,)).fetchone()[0]:
            raise RuntimeError('Analytics refresh already active')
        try:
            yield
        finally:
            conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))


def read_metadata(dataset_id):
    with get_connection() as conn:
        cursor = conn.cursor(row_factory=dict_row)
        data = cursor.execute('''SELECT d.dataset_id,d.workspace_id,d.current_state_id,d.current_gold_run_id,d.state_analytics_status,
            s.state_version,s.row_count,s.published_at AS state_updated_at,a.active_rows,
            p.load_strategy,u.source_file_name,f.file_name FROM datasets d
            LEFT JOIN dataset_state_versions s ON s.state_id=d.current_state_id
            LEFT JOIN delivery_applications a ON a.application_id=s.source_application_id
            LEFT JOIN dataset_load_policies p ON p.policy_id=s.policy_id
            LEFT JOIN upload_requests u ON u.upload_id=a.upload_request_id
            LEFT JOIN physical_files f ON f.file_id=u.file_id WHERE d.dataset_id=%s''', (dataset_id,)).fetchone()
        run = cursor.execute('SELECT * FROM dataset_gold_runs WHERE gold_run_id=%s', (data['current_gold_run_id'],)).fetchone() if data['current_gold_run_id'] else None
        latest = cursor.execute('SELECT status,failure_code,started_at FROM dataset_gold_runs WHERE dataset_id=%s AND source_state_id=%s ORDER BY gold_run_id DESC LIMIT 1', (dataset_id,data['current_state_id'])).fetchone()
        if data['state_analytics_status']=='REFRESHING':
            namespace = cursor.execute('SELECT current_schema() AS name').fetchone()['name']
            key = f"{namespace}:datarise:analytics:{data['workspace_id']}:{dataset_id}"
            available = cursor.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS available',(key,)).fetchone()['available']
            if available:
                # A dead coordinator released its session lock. Expose recovery without
                # changing metadata from a GET; the retry will resume the pinned run.
                data['state_analytics_status']='FAILED'
                latest = {'failure_code':'INTERRUPTED'}
    fresh = bool(run and run['status']=='SUCCESS' and run['source_state_id']==data['current_state_id'] and data['state_analytics_status']=='FRESH')
    return data, run, latest, fresh


def readiness(workspace_id, dataset_id, user):
    validate_scope(user,workspace_id,dataset_id)
    data,run,latest,fresh = read_metadata(dataset_id)
    return {'workspace_id': workspace_id, 'dataset_id': dataset_id, 'analytics_ready': fresh,
            'freshness': data['state_analytics_status'] or 'NOT_READY',
            'current_state_id': data['current_state_id'], 'state_version': data['state_version'],
            'trusted_rows': data['row_count'], 'analytics_rows': run['row_count'] if run else None,
            'active_rows': data['active_rows'], 'load_strategy': data['load_strategy'],
            'latest_source_file_name': data['source_file_name'] or data['file_name'],
            'state_updated_at': data['state_updated_at'], 'analytics_built_at': run['completed_at'] if run else None,
            'built_from_state_id': run['source_state_id'] if run else None,
            'gold_run_id': run['gold_run_id'] if run else None,
            'failure_code': latest['failure_code'] if latest else None,
            'can_refresh': data['current_state_id'] is not None and data['state_analytics_status'] != 'REFRESHING'}


def publish_gold(conn, dataset_id, run, key, checksum, catalog):
    cursor = conn.cursor(row_factory=dict_row)
    head = cursor.execute('SELECT current_state_id FROM datasets WHERE dataset_id=%s FOR UPDATE', (dataset_id,)).fetchone()
    cursor.execute('''UPDATE dataset_gold_runs SET status='SUCCESS',completed_at=NOW(),failure_code=NULL,
        manifest_key=%s,manifest_sha256=%s,catalog=%s,row_count=%s WHERE gold_run_id=%s''',
        (key,checksum,Jsonb(catalog),catalog[0]['row_count'],run['gold_run_id']))
    if head['current_state_id'] == run['source_state_id']:
        cursor.execute("UPDATE datasets SET current_gold_run_id=%s,state_analytics_status='FRESH' WHERE dataset_id=%s", (run['gold_run_id'],dataset_id))
    # Historical completion never replaces the current pointer or its freshness.


def refresh_analytics(workspace_id, dataset_id, user):
    validate_scope(user,workspace_id,dataset_id)
    started = time.monotonic()
    with refresh_lock(workspace_id,dataset_id):
        with repository_transaction() as conn:
            cursor = conn.cursor(row_factory=dict_row)
            head = cursor.execute('SELECT current_state_id FROM datasets WHERE dataset_id=%s FOR UPDATE', (dataset_id,)).fetchone()
            if not head['current_state_id']:
                raise ValueError('Trusted dataset update required')
            state = cursor.execute("SELECT * FROM dataset_state_versions WHERE state_id=%s AND dataset_id=%s AND status='PUBLISHED'", (head['current_state_id'],dataset_id)).fetchone()
            run = cursor.execute('SELECT * FROM dataset_gold_runs WHERE dataset_id=%s AND source_state_id=%s AND build_version=1', (dataset_id,state['state_id'])).fetchone()
            if run and run['status']=='SUCCESS':
                cursor.execute("UPDATE datasets SET current_gold_run_id=%s,state_analytics_status='FRESH' WHERE dataset_id=%s", (run['gold_run_id'],dataset_id))
                reused = True
            else:
                reused = False
                if run:
                    run = cursor.execute("UPDATE dataset_gold_runs SET status='REFRESHING',started_at=NOW(),completed_at=NULL,failure_code=NULL WHERE gold_run_id=%s RETURNING *", (run['gold_run_id'],)).fetchone()
                else:
                    run = cursor.execute('''INSERT INTO dataset_gold_runs(dataset_id,source_state_id,dataset_version_id,policy_id,source_sha256,status)
                        VALUES (%s,%s,%s,%s,%s,'REFRESHING') RETURNING *''',
                        (dataset_id,state['state_id'],state['dataset_version_id'],state['policy_id'],state['manifest_sha256'])).fetchone()
                cursor.execute("UPDATE datasets SET state_analytics_status='REFRESHING' WHERE dataset_id=%s", (dataset_id,))
            app = cursor.execute('SELECT * FROM delivery_applications WHERE application_id=%s', (state['source_application_id'],)).fetchone()
            policy = cursor.execute('SELECT * FROM dataset_load_policies WHERE policy_id=%s', (state['policy_id'],)).fetchone()
        if reused:
            return readiness(workspace_id,dataset_id,user)
        failure = 'BUILD_FAILED'
        try:
            logger.info('Dataset Gold refreshing workspace=%s dataset=%s state=%s run=%s checksum=%s freshness=REFRESHING',
                        workspace_id,dataset_id,state['state_id'],run['gold_run_id'],run['source_sha256'])
            store = CumulativeGoldArtifacts()
            # Pin the resolved state, not a subsequent head observed after IO starts.
            trusted,frame = load_pinned_state(store,state,app,policy)
            artifacts = build_cumulative_gold(frame,policy)
            key,checksum,_ = store.build(run,artifacts)
            failure = 'VALIDATION_FAILED'
            catalog = store.validate(run,key,checksum,len(artifacts[0][0]),[c['name'] for c in policy['schema_columns']])
            with repository_transaction() as conn:
                publish_gold(conn,dataset_id,run,key,checksum,catalog)
        except Exception:
            with repository_transaction() as conn:
                cursor = conn.cursor(row_factory=dict_row)
                # A response/logging failure after durable success must not regress it.
                persisted = cursor.execute('SELECT status FROM dataset_gold_runs WHERE gold_run_id=%s FOR UPDATE', (run['gold_run_id'],)).fetchone()
                if persisted['status'] != 'SUCCESS':
                    cursor.execute("UPDATE dataset_gold_runs SET status='FAILED',completed_at=NOW(),failure_code=%s WHERE gold_run_id=%s", (failure,run['gold_run_id']))
                    cursor.execute("UPDATE datasets SET state_analytics_status='FAILED' WHERE dataset_id=%s AND current_state_id=%s", (dataset_id,state['state_id']))
            logger.warning('Dataset Gold failed workspace=%s dataset=%s state=%s run=%s category=%s', workspace_id,dataset_id,state['state_id'],run['gold_run_id'],failure)
            raise RuntimeError('Analytics refresh failed; previous published analytics retained') from None
        result = readiness(workspace_id,dataset_id,user)
        logger.info('Dataset Gold completed workspace=%s dataset=%s state=%s run=%s checksum=%s artifacts=%s freshness=%s duration=%.3f',
                    workspace_id,dataset_id,state['state_id'],run['gold_run_id'],run['source_sha256'],len(catalog),result['freshness'],time.monotonic()-started)
        return result


def load_pinned_state(store,state,app,policy):
    import hashlib
    import json
    body = store.read(state['manifest_key'])
    if hashlib.sha256(body).hexdigest()!=state['manifest_sha256']:
        raise ValueError('Trusted state integrity failed')
    manifest = json.loads(body)
    columns = [c['name'] for c in policy['schema_columns']]
    frame = store.validate_state(state['manifest_key'],state['manifest_sha256'],app,columns,state['row_count'],manifest['effective_schema'],policy=policy)
    return state,frame


def current_catalog(dataset_id):
    data,run,_,fresh = read_metadata(dataset_id)
    if not fresh:
        return {'dataset_version_id': None, 'analytics_ready': False, 'base_artifact': None, 'marts': [], 'mart_count': 0}
    artifacts = run['catalog']
    for artifact in artifacts:
        for role,key in [('DIMENSION','dimensions'),('MEASURE','measures'),('METRIC','metrics')]:
            artifact[key] = [c['column_name'] for c in artifact['columns'] if c['column_role']==role]
    return {'dataset_version_id': run['dataset_version_id'], 'analytics_ready': True,
            'source_state_id': run['source_state_id'], 'gold_run_id': run['gold_run_id'],
            'base_artifact': artifacts[0], 'marts': artifacts[1:], 'mart_count': len(artifacts)-1}


def read_dashboard(workspace_id,dataset_id,user):
    from app.services.analytics_service import _build_kpis_from_catalog, _build_charts_from_catalog, _build_suggestions_from_catalog
    result = readiness(workspace_id,dataset_id,user)
    catalog = current_catalog(dataset_id)
    result.update(kpis=[],charts=[],suggested_questions=[])
    if result['analytics_ready'] and catalog['analytics_ready']:
        result.update(kpis=_build_kpis_from_catalog(catalog),charts=_build_charts_from_catalog(catalog),
                      suggested_questions=_build_suggestions_from_catalog(catalog))
        # Reject a result whose source became stale during artifact IO.
        after = readiness(workspace_id,dataset_id,user)
        if not after['analytics_ready'] or after['gold_run_id']!=catalog['gold_run_id']:
            return {**after,'kpis':[],'charts':[],'suggested_questions':[]}
        result = {**result,**after}
    return result
