"""Scoped reads for the incremental control plane; no row/data execution."""
from psycopg.rows import dict_row
from app.db.database import get_connection
from app.processing.schema_fingerprint import normalize_dtype
from app.processing.row_identity import business_column


def schema_for_version(dataset_id, version_id, file_id=None):
    with get_connection() as conn:
        version = conn.execute('SELECT schema_hash FROM dataset_versions WHERE dataset_id=%s AND dataset_version_id=%s', (dataset_id,version_id)).fetchone()
        if not version:
            raise ValueError('Schema version does not belong to this dataset')
        source = conn.execute('''SELECT f.file_id FROM dataset_version_files a JOIN physical_files f ON f.file_id=a.file_id
            WHERE a.dataset_version_id=%s AND f.schema_hash=%s AND (%s::bigint IS NULL OR f.file_id=%s)
            ORDER BY a.dataset_version_file_id DESC LIMIT 1''', (version_id,version[0],file_id,file_id)).fetchone()
        rows = conn.execute('SELECT column_name,inferred_type FROM dataset_profiles WHERE file_id=%s ORDER BY column_name', (source[0],)).fetchall() if source else []
        if not rows or any(not business_column(r[0]) for r in rows) or len({r[0].strip().lower() for r in rows}) != len(rows):
            raise ValueError('An unambiguous profiled business schema is required')
        return version[0], [{'name':r[0], 'data_type':normalize_dtype(r[1])} for r in rows]


def policies(dataset_id):
    with get_connection() as conn:
        return conn.cursor(row_factory=dict_row).execute('SELECT * FROM dataset_load_policies WHERE dataset_id=%s ORDER BY policy_version', (dataset_id,)).fetchall()


def foundation(dataset_id):
    with get_connection() as conn:
        cursor = conn.cursor(row_factory=dict_row)
        versions = cursor.execute('SELECT dataset_version_id,version_number,schema_hash FROM dataset_versions WHERE dataset_id=%s ORDER BY version_number', (dataset_id,)).fetchall()
        for version in versions:
            try:
                _, version['columns'] = schema_for_version(dataset_id,version['dataset_version_id'])
            except ValueError:
                version['columns'] = []
        head = cursor.execute('''SELECT s.state_id,s.dataset_version_id,s.policy_id,s.state_version,s.row_count,s.published_at,d.state_analytics_status
            FROM datasets d JOIN dataset_state_versions s ON s.state_id=d.current_state_id WHERE d.dataset_id=%s''', (dataset_id,)).fetchone()
        applications = cursor.execute('''SELECT a.application_id,a.upload_request_id,a.dataset_version_id,a.dataset_version_file_id,
            a.policy_id,a.applied_rule_version,a.source_dq_run_id,a.status,a.ingestion_time,a.started_at,a.completed_at,
            a.input_rows,a.valid_rows,a.rejected_rows,a.inserted_rows,a.updated_rows,a.unchanged_rows,a.duplicate_rows,
            a.deactivated_rows,a.current_state_rows,a.failure_code,a.result_state_id,a.incremental_rejected_rows,
            p.policy_version,p.load_strategy,p.business_keys,u.archived_at,f.file_name AS source_file_name
            FROM delivery_applications a JOIN dataset_load_policies p ON p.policy_id=a.policy_id
            JOIN upload_requests u ON u.upload_id=a.upload_request_id JOIN physical_files f ON f.file_id=u.file_id WHERE a.dataset_id=%s ORDER BY a.application_id''', (dataset_id,)).fetchall()
        return {'schema_versions':versions, 'policies':policies(dataset_id), 'current_state':head, 'applications':applications}


def application_context(context):
    """Logical delivery projection; never modifies the shared processing association."""
    with get_connection() as conn:
        cursor=conn.cursor(row_factory=dict_row)
        application=cursor.execute('SELECT * FROM delivery_applications WHERE upload_request_id=%s',(context['upload_request_id'],)).fetchone()
        policy=cursor.execute('SELECT * FROM dataset_load_policies WHERE dataset_id=%s ORDER BY policy_version DESC LIMIT 1',(context['dataset_id'],)).fetchone()
        if application:
            policy=cursor.execute('SELECT * FROM dataset_load_policies WHERE policy_id=%s',(application['policy_id'],)).fetchone()
        head=cursor.execute('SELECT s.policy_id,s.dataset_version_id FROM datasets d JOIN dataset_state_versions s ON s.state_id=d.current_state_id WHERE d.dataset_id=%s',(context['dataset_id'],)).fetchone()
    if not policy: return context
    context['load_strategy']=policy['load_strategy']
    context['application']=application
    stage='PENDING'
    if application: stage={'PREPARED':'PENDING','RUNNING':'PROCESSING','SUCCESS':'SUCCESS','FAILED':'FAILED'}[application['status']]
    blocked=policy['load_strategy']!='APPEND' or (context['dataset_version_id'] is not None and context['dataset_version_id']!=policy['dataset_version_id']) or (head is not None and (head['policy_id']!=policy['policy_id'] or head['dataset_version_id']!=policy['dataset_version_id']))
    if blocked and context['stages']['silver']=='SUCCESS':
        stage='BLOCKED'; context['status']='DATASET_UPDATE_BLOCKED'; context['can_continue']=False
        context['error_summary']='Dataset update requires an executable APPEND policy and compatible schema. Review Contract.'
    elif application and application['status'] in ('RUNNING','FAILED'):
        context['status']='DATASET_UPDATE_PROCESSING' if stage=='PROCESSING' else 'DATASET_UPDATE_FAILED'
        context['can_continue']=not context['archived_at'] and stage=='FAILED'
        context['error_summary']=None if stage=='PROCESSING' else 'Dataset update could not be published. Your validated delivery is safe; the previous trusted dataset version is still active.'
    elif context['stages']['silver']=='SUCCESS' and stage!='SUCCESS':
        context['status']='READY_TO_APPLY'; context['can_continue']=not context['archived_at']
        context['completed_at']=None; context['output_rows']=None
    context['stages']['dataset_update']=stage
    if application and application['status']=='SUCCESS':
        context['inserted_rows']=application['inserted_rows']; context['duplicate_rows']=application['duplicate_rows']
        context['incremental_rejected_rows']=application['incremental_rejected_rows']; context['current_state_rows']=application['current_state_rows']
        if context['status'] in ('SUCCESS','SUCCESS_WITH_WARNINGS') and application['incremental_rejected_rows']: context['status']='SUCCESS_WITH_WARNINGS'
    if stage!='SUCCESS': context['completed_at']=None
    return context
