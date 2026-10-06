"""Dataset-scoped APPEND coordination. Engine/storage are separate adapters."""
import hashlib
import io
import json
import logging
import time
from datetime import datetime,timezone
from uuid import uuid4
import pandas as pd
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.config import S3_BUCKET_NAME
from app.db.database import get_connection,repository_transaction
from app.db.incremental_repository import policies
from app.processing.append_engine import append_rows,effective_schema,LINEAGE
from app.services.incremental_application_service import prepare_application,_publish_metadata,public_application
from app.services.processing_context_service import validate_scope,owned_upload
from app.services.dataset_processing_service import dataset_lock
from app.services.delivery_lifecycle_service import lifecycle_lock,require_active
from app.storage.incremental_artifacts import IncrementalArtifacts

logger=logging.getLogger(__name__)


def selected_policy(dataset_id,upload_id):
    with get_connection() as conn:
        pinned=conn.execute('SELECT policy_id FROM delivery_applications WHERE upload_request_id=%s',(upload_id,)).fetchone()
    options=policies(dataset_id)
    return next((p for p in options if p['policy_id']==pinned[0]),None) if pinned else options[-1] if options else None


def artifact_key(path):
    prefix=f's3://{S3_BUCKET_NAME}/'
    if path.startswith(prefix): return path[len(prefix):]
    if path.startswith('silver/') and '..' not in path and '\\' not in path: return path
    raise ValueError('Silver object outside configured data plane')


def delivery_manifest(store,application,policy,file_id):
    with get_connection() as conn:
        cursor=conn.cursor(row_factory=dict_row)
        existing=cursor.execute('SELECT * FROM delivery_manifests WHERE application_id=%s',(application['application_id'],)).fetchone()
        dq=cursor.execute('SELECT * FROM data_quality_runs WHERE dq_run_id=%s',(application['source_dq_run_id'],)).fetchone()
    if existing:
        manifest_body=store.read(existing['manifest_key'])
        if hashlib.sha256(manifest_body).hexdigest()!=existing['manifest_sha256']: raise ValueError('Delivery manifest integrity failed')
        manifest=json.loads(manifest_body)
        for field in ('application_id','upload_request_id','dataset_id','dataset_version_id','policy_id','source_dq_run_id','applied_rule_version'):
            if manifest[field]!=application[field]: raise ValueError('Delivery manifest pin differs')
        if manifest['policy']!=json.loads(store.json(policy)) or manifest['silver_key']!=existing['silver_key'] or manifest['silver_sha256']!=existing['silver_sha256']:
            raise ValueError('Manifest policy/input differs')
        frame=store.frame(existing['silver_key'],existing['silver_sha256'])
        if len(frame)!=application['valid_rows'] or effective_schema(frame,[c['name'] for c in policy['schema_columns']])!=existing['effective_schema']:
            raise ValueError('Pinned Silver output differs')
        return existing,frame
    # Copy exact successful bytes, never a mutable reference to the legacy Silver path.
    source_key=artifact_key(dq['silver_path'])
    expected=f"silver/dataset_version_id={application['dataset_version_id']}/file_id={file_id}/rule_version={application['applied_rule_version']}/data.parquet"
    if source_key!=expected: raise ValueError('Silver artifact lineage differs')
    body=store.read(source_key)
    frame=pd.read_parquet(io.BytesIO(body))
    columns=[c['name'] for c in policy['schema_columns']]
    if len(frame)!=application['valid_rows'] or sorted(c for c in frame if not c.startswith(('_dq_','_source_','_bronze_','_gold_','_datarise_')))!=sorted(columns):
        raise ValueError('Successful Silver rows/schema differ from policy')
    frame=frame[columns].copy(); schema=effective_schema(frame,columns)
    if application['input_rows']!=application['valid_rows']+application['rejected_rows']:
        raise ValueError('Silver outcome accounting differs')
    prefix=f"silver/dataset_id={application['dataset_id']}/dataset_version_id={application['dataset_version_id']}/delivery/upload_request_id={application['upload_request_id']}/application_id={application['application_id']}/manifest-{uuid4().hex}/"
    silver_key=prefix+'data.parquet'; silver_hash=store.put(silver_key,store.parquet(frame))
    manifest={**application,'physical_file_id':file_id,'source_silver_sha256':hashlib.sha256(body).hexdigest(),'manifest_version':1,'status':'READY_TO_APPLY','policy':policy,
              'load_strategy':policy['load_strategy'],'business_keys':policy['business_keys'],'normalization_version':policy['normalization_version'],
              'silver_key':silver_key,'silver_sha256':silver_hash,'effective_schema':schema,'created_at':datetime.now(timezone.utc).isoformat()}
    manifest_key=prefix+'manifest.json'; manifest_hash=store.put(manifest_key,store.json(manifest))
    store.frame(silver_key,silver_hash)  # Verify readability before registering READY_TO_APPLY.
    with repository_transaction() as conn:
        row=conn.cursor(row_factory=dict_row).execute('''INSERT INTO delivery_manifests(application_id,manifest_key,manifest_sha256,silver_key,silver_sha256,effective_schema)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING *''',(application['application_id'],manifest_key,manifest_hash,silver_key,silver_hash,Jsonb(schema))).fetchone()
    return row,frame


def current_state(store,application,policy):
    with get_connection() as conn:
        state=conn.cursor(row_factory=dict_row).execute('SELECT s.* FROM datasets d JOIN dataset_state_versions s ON s.state_id=d.current_state_id WHERE d.dataset_id=%s',(application['dataset_id'],)).fetchone()
        historical=conn.cursor(row_factory=dict_row).execute('SELECT * FROM delivery_applications WHERE application_id=%s',(state['source_application_id'],)).fetchone() if state else None
    if not state: return None,None
    if state['policy_id']!=application['policy_id'] or state['dataset_version_id']!=application['dataset_version_id']:
        raise ValueError('Explicit state migration required')
    body=store.read(state['manifest_key'])
    if hashlib.sha256(body).hexdigest()!=state['manifest_sha256']: raise ValueError('Trusted state manifest integrity failed')
    manifest=json.loads(body)
    # Validate historical ownership using the historical source application, not this arrival.
    columns=[c['name'] for c in policy['schema_columns']]
    return state,store.validate_state(state['manifest_key'],state['manifest_sha256'],historical,columns,state['row_count'],manifest['effective_schema'])


def apply_append(workspace_id,dataset_id,user,upload_id):
    validate_scope(user,workspace_id,dataset_id)
    upload=owned_upload(upload_id,user,workspace_id,dataset_id)
    with dataset_lock(workspace_id,dataset_id),lifecycle_lock(upload_id):
        policy=selected_policy(dataset_id,upload_id)
        if not policy or policy['load_strategy']!='APPEND': raise ValueError('An explicit APPEND policy is required')
        with get_connection() as conn:
            existing=conn.cursor(row_factory=dict_row).execute('SELECT * FROM delivery_applications WHERE upload_request_id=%s',(upload_id,)).fetchone()
        if existing and existing['status']=='SUCCESS': return public_application(existing)
        require_active(upload_id)
        application=prepare_application(workspace_id,dataset_id,user,upload_id,policy['policy_id'])
        correlation=uuid4().hex
        started=time.monotonic()
        try:
            with repository_transaction() as conn:
                application=conn.cursor(row_factory=dict_row).execute("UPDATE delivery_applications SET status='RUNNING',started_at=NOW(),completed_at=NULL,failure_code=NULL WHERE application_id=%s RETURNING *",(application['application_id'],)).fetchone()
            store=IncrementalArtifacts()
            manifest,incoming=delivery_manifest(store,application,policy,upload[2])
            head,current=current_state(store,application,policy)
            columns=[c['name'] for c in policy['schema_columns']]
            if current is None: current=pd.DataFrame(columns=columns+LINEAGE)
            if len(current) and effective_schema(current,columns)!=manifest['effective_schema']: raise ValueError('Effective Silver schema requires migration')
            candidate,counts,conflicts=append_rows(current,incoming,policy,application)
            if len(current): pd.testing.assert_frame_equal(candidate.iloc[:len(current)].reset_index(drop=True),current.reset_index(drop=True),check_dtype=False,check_exact=True)
            schema=effective_schema(candidate,columns)
            prefix=f"silver/dataset_id={dataset_id}/dataset_version_id={application['dataset_version_id']}/state/application_id={application['application_id']}/candidate-{uuid4().hex}/"
            data_key=prefix+'data.parquet'; data_hash=store.put(data_key,store.parquet(candidate))
            conflict_key=prefix+'outcomes.json'; conflict_hash=store.put(conflict_key,store.json({'rejected':conflicts}))
            state_manifest={**{k:application[k] for k in ('application_id','dataset_id','dataset_version_id','policy_id','upload_request_id')},
                'previous_state_id':head['state_id'] if head else None,'delivery_manifest_id':manifest['manifest_id'],
                'data_key':data_key,'data_sha256':data_hash,'row_count':len(candidate),'effective_schema':schema,
                'normalization_version':1,'outcomes_key':conflict_key,'outcomes_sha256':conflict_hash,'counts':counts}
            manifest_key=prefix+'manifest.json'; manifest_hash=store.put(manifest_key,store.json(state_manifest))
            with repository_transaction() as conn:
                state_id=conn.execute('''INSERT INTO dataset_state_versions(dataset_id,dataset_version_id,policy_id,source_application_id,previous_state_id,state_version,manifest_key,manifest_sha256)
                    VALUES (%s,%s,%s,%s,%s,(SELECT COALESCE(MAX(state_version),0)+1 FROM dataset_state_versions WHERE dataset_id=%s),%s,%s) RETURNING state_id''',
                    (dataset_id,application['dataset_version_id'],policy['policy_id'],application['application_id'],head['state_id'] if head else None,dataset_id,manifest_key,manifest_hash)).fetchone()[0]
            store.validate_state(manifest_key,manifest_hash,application,columns,len(candidate),schema)
            if len(candidate)!=len(current)+counts['inserted_rows']: raise ValueError('APPEND state count invariant failed')
            with repository_transaction() as conn:
                conn.execute("UPDATE dataset_state_versions SET status='VALIDATED',validated_at=NOW(),row_count=%s WHERE state_id=%s",(len(candidate),state_id))
                conn.execute('''UPDATE delivery_applications SET inserted_rows=%s,duplicate_rows=%s,incremental_rejected_rows=%s,
                    updated_rows=NULL,unchanged_rows=NULL,deactivated_rows=NULL WHERE application_id=%s''',
                    (counts['inserted_rows'],counts['duplicate_rows'],counts['incremental_rejected_rows'],application['application_id']))
                result=_publish_metadata(conn,workspace_id,dataset_id,user,application['application_id'])
                conn.execute("UPDATE datasets SET state_analytics_status='STALE' WHERE dataset_id=%s",(dataset_id,))
            # Log success/return only after COMMIT; response-loss retry reads durable success.
            logger.info('APPEND status=SUCCESS correlation=%s workspace=%s dataset=%s upload=%s application=%s source_state=%s state=%s policy=%s inserted=%s duplicate=%s rejected=%s duration=%.3f',correlation,workspace_id,dataset_id,upload_id,application['application_id'],head['state_id'] if head else None,state_id,policy['policy_id'],counts['inserted_rows'],counts['duplicate_rows'],counts['incremental_rejected_rows'],time.monotonic()-started)
            return result
        except Exception as exc:
            with repository_transaction() as conn:
                committed=conn.execute("SELECT status FROM delivery_applications WHERE application_id=%s",(application['application_id'],)).fetchone()[0]=='SUCCESS'
                conn.execute("UPDATE delivery_applications SET status='FAILED',failure_code='APPLICATION_FAILED',completed_at=NOW() WHERE application_id=%s AND status <> 'SUCCESS'",(application['application_id'],))
            if committed:
                raise RuntimeError('Dataset update completed; response interrupted. Refresh or retry to retrieve the saved result') from exc
            logger.warning('APPEND failed correlation=%s dataset=%s upload=%s application=%s',correlation,dataset_id,upload_id,application['application_id'])
            raise RuntimeError('Dataset update could not be published; previous trusted state remains active') from exc
