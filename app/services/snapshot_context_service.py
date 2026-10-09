"""Explicit per-delivery declarations; no guessed coverage or business time."""
from psycopg.rows import dict_row
from app.db.database import get_connection, repository_transaction
from app.services.processing_context_service import validate_scope, owned_upload
from app.services.dataset_processing_service import dataset_lock
from app.services.delivery_lifecycle_service import lifecycle_lock, require_active
from app.services.incremental_policy_service import IncrementalConflict


def read_snapshot_context(upload_id):
    with get_connection() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            'SELECT coverage,effective_at,delivery_kind FROM snapshot_delivery_contexts WHERE upload_request_id=%s',
            (upload_id,)).fetchone()


def declare_snapshot(workspace_id, dataset_id, user, upload_id, request):
    validate_scope(user, workspace_id, dataset_id)
    owned_upload(upload_id, user, workspace_id, dataset_id)
    with dataset_lock(workspace_id, dataset_id), lifecycle_lock(upload_id), repository_transaction() as conn:
        require_active(upload_id)
        cursor = conn.cursor(row_factory=dict_row)
        policy = cursor.execute('SELECT * FROM dataset_load_policies WHERE dataset_id=%s ORDER BY policy_version DESC LIMIT 1', (dataset_id,)).fetchone()
        if not policy or policy['load_strategy'] != 'SNAPSHOT':
            raise ValueError('SNAPSHOT policy required')
        if request.coverage == 'COMPLETE' and policy['snapshot_coverage'] != 'COMPLETE':
            raise ValueError('Partial policy cannot authorize complete delivery')
        if policy['event_time_column'] and request.effective_at is None:
            raise ValueError('Business-time snapshot requires explicit effective time')
        existing = read_snapshot_context(upload_id)
        settings = request.model_dump()
        if existing:
            if dict(existing) != settings:
                raise IncrementalConflict('Snapshot declaration already pinned')
            return {'workspace_id':workspace_id,'dataset_id':dataset_id,'upload_request_id':upload_id,**existing}
        if cursor.execute('SELECT 1 FROM delivery_applications WHERE upload_request_id=%s', (upload_id,)).fetchone():
            raise IncrementalConflict('Application context already pinned')
        cursor.execute('INSERT INTO snapshot_delivery_contexts(upload_request_id,coverage,effective_at,delivery_kind) VALUES (%s,%s,%s,%s)',
                       (upload_id, request.coverage, request.effective_at, request.delivery_kind))
        return {'workspace_id':workspace_id,'dataset_id':dataset_id,'upload_request_id':upload_id,**settings}
