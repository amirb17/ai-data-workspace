"""Explicit backend load policies, independent of browser contracts and DQ rules."""
from psycopg.types.json import Jsonb
import logging
from app.db.database import repository_transaction
from app.db.incremental_repository import schema_for_version, policies, foundation
from app.services.processing_context_service import validate_scope
from app.services.dataset_processing_service import dataset_lock


class IncrementalConflict(RuntimeError):
    pass


def read_foundation(workspace_id,dataset_id,user):
    validate_scope(user,workspace_id,dataset_id)
    with repository_transaction() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return {'workspace_id':workspace_id, 'dataset_id':dataset_id, 'execution_available':True, 'executable_modes':['APPEND'], **foundation(dataset_id)}


def save_policy(workspace_id,dataset_id,user,request):
    validate_scope(user,workspace_id,dataset_id)
    with dataset_lock(workspace_id,dataset_id), repository_transaction() as conn:
        schema_hash, columns = schema_for_version(dataset_id,request.dataset_version_id)
        names = {c['name']:c['data_type'] for c in columns}
        if any(key not in names for key in request.business_keys):
            raise ValueError('Business keys must exist in this profiled schema')
        if request.event_time_column is not None and names.get(request.event_time_column) not in ('DATE','DATETIME'):
            raise ValueError('Event time requires a compatible date/time column')
        current = policies(dataset_id)
        latest = current[-1] if current else None
        settings = {'dataset_version_id':request.dataset_version_id, 'load_strategy':request.load_strategy.value,
                    'business_keys':request.business_keys, 'schema_evolution_policy':request.schema_evolution_policy,
                    'event_time_column':request.event_time_column, 'schema_hash':schema_hash, 'schema_columns':columns}
        if latest and all(latest[k] == v for k,v in settings.items()):
            return latest  # Response retry does not create another policy version.
        actual_version = latest['policy_version'] if latest else 0
        if actual_version != request.expected_policy_version:
            raise IncrementalConflict('Policy changed; refresh before saving')
        if latest and not request.confirm_policy_change:
            raise IncrementalConflict('Explicit prospective policy change confirmation required')
        head = conn.execute('SELECT current_state_id FROM datasets WHERE dataset_id=%s FOR UPDATE',(dataset_id,)).fetchone()[0]
        if head is not None:
            raise IncrementalConflict('Changing a published state policy requires a future migration flow')
        row = conn.execute('''INSERT INTO dataset_load_policies(dataset_id,dataset_version_id,policy_version,load_strategy,
            business_keys,schema_evolution_policy,event_time_column,schema_hash,schema_columns,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING policy_id''',
            (dataset_id,request.dataset_version_id,actual_version+1,request.load_strategy.value,Jsonb(request.business_keys),
             request.schema_evolution_policy,request.event_time_column,schema_hash,Jsonb(columns),user['user_id'])).fetchone()
        logging.getLogger(__name__).info('Load policy prepared workspace=%s dataset=%s policy=%s version=%s',workspace_id,dataset_id,row[0],actual_version+1)
        return next(p for p in policies(dataset_id) if p['policy_id']==row[0])
