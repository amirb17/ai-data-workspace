"""Pinned delivery preparation and shared internal atomic metadata publication."""
from psycopg.rows import dict_row
import logging
from app.db.database import get_connection, repository_transaction
from app.db.incremental_repository import schema_for_version
from app.services.dataset_processing_service import dataset_lock
from app.services.delivery_lifecycle_service import lifecycle_lock, require_active
from app.services.processing_context_service import owned_upload, read_processing_context, validate_scope
from app.services.incremental_policy_service import IncrementalConflict


def prepare_application(workspace_id,dataset_id,user,upload_id,policy_id):
    validate_scope(user,workspace_id,dataset_id)
    upload = owned_upload(upload_id,user,workspace_id,dataset_id)
    with dataset_lock(workspace_id,dataset_id), lifecycle_lock(upload_id), repository_transaction() as conn:
        require_active(upload_id)
        context = read_processing_context(upload_id,user,workspace_id,dataset_id)
        cursor = conn.cursor(row_factory=dict_row)
        policy = cursor.execute('SELECT * FROM dataset_load_policies WHERE policy_id=%s AND dataset_id=%s',(policy_id,dataset_id)).fetchone()
        if not policy or policy['dataset_version_id'] != context['dataset_version_id']:
            raise ValueError('Policy and delivery must share a dataset/schema version')
        existing = cursor.execute('SELECT * FROM delivery_applications WHERE upload_request_id=%s',(upload_id,)).fetchone()
        if existing:
            if existing['policy_id'] != policy_id:
                raise IncrementalConflict('Delivery is already pinned to another policy; explicit replay is not available')
            return public_application(existing)
        schema_hash,columns = schema_for_version(dataset_id,context['dataset_version_id'],context['file_id'])
        if schema_hash != policy['schema_hash'] or columns != policy['schema_columns']:
            raise ValueError('Delivery schema differs from the pinned policy; explicit schema migration required')
        if context['stages']['silver'] != 'SUCCESS' or context['rule_state'] != 'FINALIZED':
            raise ValueError('Approved replayable Silver output required before application preparation')
        dq = cursor.execute('''SELECT dq.* FROM data_quality_runs dq JOIN processing_attempts pa ON pa.attempt_id=dq.attempt_id
            WHERE dq.dataset_version_file_id=%s AND dq.rule_version=%s AND pa.status='SUCCESS'
            ORDER BY dq.dq_run_id DESC LIMIT 1''', (context['dataset_version_file_id'],context['rule_version'])).fetchone()
        if not dq or not dq['silver_path']:
            raise ValueError('Replayable Silver output unavailable')
        head = cursor.execute('''SELECT s.dataset_version_id,s.policy_id FROM datasets d JOIN dataset_state_versions s
            ON s.state_id=d.current_state_id WHERE d.dataset_id=%s''',(dataset_id,)).fetchone()
        if head and (head['dataset_version_id'] != policy['dataset_version_id'] or head['policy_id'] != policy_id):
            raise IncrementalConflict('Current state uses another schema or policy; explicit migration required')
        snapshot = None
        if policy['load_strategy'] == 'SNAPSHOT':
            from app.services.snapshot_context_service import read_snapshot_context
            snapshot = read_snapshot_context(upload_id)
            if not snapshot:
                raise ValueError('Explicit snapshot delivery declaration required')
            if snapshot['coverage'] == 'COMPLETE' and policy['snapshot_coverage'] != 'COMPLETE':
                raise ValueError('Complete coverage not authorized by pinned policy')
            if policy['event_time_column'] and snapshot['effective_at'] is None:
                raise ValueError('Snapshot effective time required')
        row = cursor.execute('''INSERT INTO delivery_applications(upload_request_id,dataset_id,dataset_version_id,dataset_version_file_id,
            policy_id,applied_rule_version,source_dq_run_id,ingestion_time,input_rows,valid_rows,rejected_rows,
            snapshot_coverage,snapshot_effective_at,delivery_kind)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (upload_id,dataset_id,context['dataset_version_id'],context['dataset_version_file_id'],policy_id,context['rule_version'],
             dq['dq_run_id'],upload[6],dq['total_rows'],dq['valid_rows'],dq['rejected_rows'],
             snapshot['coverage'] if snapshot else None,snapshot['effective_at'] if snapshot else None,
             snapshot['delivery_kind'] if snapshot else None)).fetchone()
        logging.getLogger(__name__).info('Application prepared workspace=%s dataset=%s upload=%s application=%s policy=%s',workspace_id,dataset_id,upload_id,row['application_id'],policy_id)
        return public_application(row)


def public_application(row):
    return {k:v for k,v in row.items() if k != 'created_at'}  # Only IDs, lifecycle and counts; no source paths.


def publish_validated_state(workspace_id,dataset_id,user,application_id):
    """Internal future-engine boundary; caller must have written/validated a candidate.

    Candidate storage validation is intentionally not implemented/exposed in 6A.
    Test fixtures exercise metadata CAS/rollback, not APPEND/UPSERT execution.
    """
    validate_scope(user,workspace_id,dataset_id)
    with dataset_lock(workspace_id,dataset_id):
        with get_connection() as conn:
            upload = conn.execute('SELECT upload_request_id FROM delivery_applications WHERE application_id=%s AND dataset_id=%s',(application_id,dataset_id)).fetchone()
        if not upload:
            raise LookupError('Application not found')
        # Hold the session lifecycle lock through the transaction COMMIT, not just the writes.
        with lifecycle_lock(upload[0]), repository_transaction() as conn:
            return _publish_metadata(conn,workspace_id,dataset_id,user,application_id)


def _publish_metadata(conn,workspace_id,dataset_id,user,application_id):
    cursor = conn.cursor(row_factory=dict_row)
    app = cursor.execute('SELECT * FROM delivery_applications WHERE application_id=%s AND dataset_id=%s FOR UPDATE',(application_id,dataset_id)).fetchone()
    if not app:
        raise LookupError('Application not found')
    owned_upload(app['upload_request_id'],user,workspace_id,dataset_id)
    if app['status']=='SUCCESS':
        return public_application(app)
    require_active(app['upload_request_id'])
    state = cursor.execute("SELECT * FROM dataset_state_versions WHERE source_application_id=%s AND status='VALIDATED' ORDER BY state_id DESC LIMIT 1 FOR UPDATE",(application_id,)).fetchone()
    if not state or state['status'] != 'VALIDATED' or app['status'] != 'RUNNING':
        raise IncrementalConflict('Running application and validated candidate are required')
    expected_prefix = f"silver/dataset_id={dataset_id}/dataset_version_id={app['dataset_version_id']}/state/application_id={application_id}/"
    if not state['manifest_key'].startswith(expected_prefix) or '..' in state['manifest_key'] or '\\' in state['manifest_key']:
        raise ValueError('Candidate must use an immutable scoped application prefix')
    head = cursor.execute('''SELECT d.current_state_id,s.dataset_version_id,s.policy_id FROM datasets d LEFT JOIN dataset_state_versions s
        ON s.state_id=d.current_state_id WHERE d.dataset_id=%s FOR UPDATE OF d''',(dataset_id,)).fetchone()
    if head['current_state_id'] != state['previous_state_id'] or (head['current_state_id'] is not None and
            (head['dataset_version_id'] != app['dataset_version_id'] or head['policy_id'] != app['policy_id'])):
        raise IncrementalConflict('Trusted state changed or requires explicit schema/policy migration')
    cursor.execute("UPDATE dataset_state_versions SET status='PUBLISHED',published_at=NOW() WHERE state_id=%s",(state['state_id'],))
    cursor.execute('UPDATE datasets SET current_state_id=%s WHERE dataset_id=%s',(state['state_id'],dataset_id))
    row = cursor.execute("""UPDATE delivery_applications SET status='SUCCESS',completed_at=NOW(),result_state_id=%s,
        current_state_rows=%s,failure_code=NULL WHERE application_id=%s RETURNING *""",(state['state_id'],state['row_count'],application_id)).fetchone()
    return public_application(row)
