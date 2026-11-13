"""Owned read-only product boundaries. No SDK clients, dispatch or publication."""
import base64
import csv
import hashlib
import io
from threading import BoundedSemaphore

from app.db import database
from app.db.processing_integration_repository import row
from app.processing.numeric_preview import preview_exact_csv, MAX_PREVIEW_BYTES, MAX_PREVIEW_ROWS, MAX_PREVIEW_COLUMNS
from app.storage.versioned_artifacts import ObjectIdentity, validate_csv_source

_compute = BoundedSemaphore(1)


class PreviewBusy(Exception): pass
class SourceMismatch(Exception): pass


def _scope(conn, w, d, user, *, lock=True):
    conn.execute("SET LOCAL statement_timeout = '5s'")
    conn.execute("SET LOCAL lock_timeout = '1s'")
    # Revalidate the authenticated actor against both current owners. Locks keep
    # ownership from changing during bounded computation; no data is modified.
    found = conn.execute("""SELECT d.current_state_id FROM users u
        JOIN workspaces w ON w.workspace_id=%s
        JOIN datasets d ON d.workspace_id=w.workspace_id AND d.dataset_id=%s
        WHERE u.user_id=%s AND u.owner_key=%s AND w.owner=u.owner_key AND d.owner=u.owner_key
          AND u.account_status='ACTIVE' AND w.status='ACTIVE' AND d.status='ACTIVE'
        """ + (" FOR SHARE OF u,w,d" if lock else ""), (w,d,user['user_id'],user['owner_key'])).fetchone()
    if found is None: raise PermissionError('Scope unavailable')
    return found[0]


def _upload(conn, w, d, upload):
    source = row(conn, """SELECT u.file_id,p.file_hash,p.file_size FROM upload_requests u
        JOIN physical_files p USING(file_id)
        WHERE u.upload_id=%s AND u.workspace_id=%s AND u.dataset_id=%s
          AND u.status='UPLOADED' AND u.archived_at IS NULL
        FOR SHARE OF u,p""", (upload,w,d))
    if source is None: raise PermissionError('Upload unavailable')
    return source


def authorize(w, d, user, upload=None):
    with database.repository_transaction() as conn:
        _scope(conn,w,d,user)
        if upload is not None: _upload(conn,w,d,upload)


def authorize_read(w, d, user):
    """Read-only authorization for dataset GET routes on a SELECT-only API role.

    The public route guard checks current owner/account/status. Individual GET
    handlers independently enforce dataset/workspace ownership. Row locks are
    retained by authorize() for preview operations where they are required.
    """
    with database.repository_transaction() as conn:
        _scope(conn, w, d, user, lock=False)


def capabilities(w,d,user):
    with database.repository_transaction() as conn:
        # This is metadata-only GET; the public API uses a SELECT-only role.
        # Do not require UPDATE privilege for a FOR SHARE owner lock.
        state = _scope(conn,w,d,user,lock=False)
        models = conn.execute('''SELECT c.warehouse_model_id,c.warehouse_model_version_id,m.display_name,v.version_number
            FROM warehouse_model_configurations c JOIN warehouse_models m USING(workspace_id,warehouse_model_id)
            JOIN warehouse_model_versions v USING(workspace_id,warehouse_model_id,warehouse_model_version_id)
            WHERE c.workspace_id=%s AND c.source_dataset_id=%s AND v.status='APPROVED' AND m.status<>'ARCHIVED'
            ORDER BY m.display_name,v.version_number LIMIT 20''',(w,d)).fetchall()
        configured = conn.execute('''SELECT EXISTS(SELECT 1 FROM warehouse_model_sources s
            JOIN warehouse_model_versions v USING(workspace_id,warehouse_model_id,warehouse_model_version_id)
            WHERE s.workspace_id=%s AND s.source_dataset_id=%s AND v.status='APPROVED')''',(w,d)).fetchone()[0]
    return dict(workspace_id=w,dataset_id=d,warehouse='NOT_CONFIGURED' if not configured else
                'ACCEPTED_STATE_REQUIRED' if state is None else 'RECONCILIATION_REQUIRED',
                warehouse_models=[dict(model_id=m,version_id=v,name=n,version_number=k) for m,v,n,k in models])


def jobs(w,d,user):
    with database.repository_transaction() as conn:
        _scope(conn,w,d,user,lock=False)
        values = conn.execute('''SELECT job_id,operation,status,attempt_count FROM processing_jobs
            WHERE workspace_id=%s AND dataset_id=%s ORDER BY created_at DESC,job_id LIMIT 20''',(w,d)).fetchall()
    return dict(workspace_id=w,dataset_id=d,jobs=[dict(job_id=str(j),operation=o,status=s,attempt_count=a) for j,o,s,a in values])


def _unavailable(w,d,reason):
    return dict(workspace_id=w,dataset_id=d,status='UNAVAILABLE',reason=reason)


def csv_preview(w,d,upload,user,request):
    with database.repository_transaction() as conn:
        _scope(conn,w,d,user)
        source = _upload(conn,w,d,upload)
        registered = row(conn,'SELECT identity FROM processing_source_objects WHERE file_id=%s',(source['file_id'],))
        if registered is None: return _unavailable(w,d,'REGISTERED_SOURCE_REQUIRED')
        pin = row(conn,'''SELECT f.applied_rule_version,p.rules FROM dataset_version_files f
            JOIN dataset_versions v USING(dataset_version_id)
            JOIN physical_files pf ON pf.file_id=f.file_id AND pf.schema_hash=v.schema_hash
            JOIN approved_rule_policies p ON p.dataset_version_id=v.dataset_version_id AND p.rule_version=f.applied_rule_version
            WHERE f.file_id=%s AND v.dataset_id=%s''',(source['file_id'],d))
        if not pin or not pin['rules']: return _unavailable(w,d,'RULE_APPROVAL_REQUIRED')
        identity = ObjectIdentity.decode(registered['identity'])
        if identity.sha256 != source['file_hash'] or identity.size != source['file_size']:
            return _unavailable(w,d,'SOURCE_REGISTRATION_UNAVAILABLE')
        if identity.size > MAX_PREVIEW_BYTES: return _unavailable(w,d,'PREVIEW_LIMIT_EXCEEDED')
        try: body = base64.b64decode(request.csv_base64,validate=True)
        except ValueError: raise SourceMismatch() from None
        if len(body)!=identity.size or hashlib.sha256(body).hexdigest()!=identity.sha256: raise SourceMismatch()
        rules = [(None,None,r['column_name'],r['rule_type'],r['rule_config'],True) for r in pin['rules']]
        exact = [r[2] for r in rules if r[3]=='DATA_TYPE' and r[4].get('mode')=='EXACT']
        legacy_money = any(r[3]=='DATA_TYPE' and r[4].get('type')=='DECIMAL' and r[4].get('mode')!='EXACT' for r in rules)
        if request.mode=='exact' and (not exact or legacy_money): return _unavailable(w,d,'EXACT_APPROVED_POLICY_REQUIRED')
        if not _compute.acquire(blocking=False): raise PreviewBusy()
        try:
            if exact:
                result = preview_exact_csv(body,identity,rules,exact)
                total,valid,rejected = result.total_rows,result.valid_rows,result.rejected_rows
                issues = [dict(column=c,rule=r,count=n) for c,r,n in result.issues]
            else:
                from app.processing.silver_transformer import apply_common_cleaning,apply_transformations
                from app.processing.silver_validator import apply_business_rules,calculate_dq_issue_counts
                import pandas as pd
                validate_csv_source(body)
                reader = csv.reader(io.StringIO(body.decode('utf-8-sig')))
                if len(next(reader))>MAX_PREVIEW_COLUMNS or sum(1 for _ in reader)>MAX_PREVIEW_ROWS:
                    return _unavailable(w,d,'PREVIEW_LIMIT_EXCEEDED')
                frame = pd.read_csv(io.BytesIO(body))
                if frame.empty: return _unavailable(w,d,'EMPTY_SOURCE')
                frame,errors,nulls = apply_transformations(apply_common_cleaning(frame),rules)
                checked = apply_business_rules(frame,rules,errors,nulls)
                rejected_frame = checked[~checked['_dq_is_valid']]
                total,rejected = len(checked),len(rejected_frame)
                valid = total-rejected
                issues = [dict(column=i['column_name'],rule=i['rule_type'],count=i['violation_count']) for i in calculate_dq_issue_counts(rejected_frame)]
            columns = next(csv.reader(io.StringIO(body.decode('utf-8-sig'))))
            return dict(workspace_id=w,dataset_id=d,status='PREVIEW',rule_version=pin['applied_rule_version'],
                        total_rows=total,valid_rows=valid,rejected_rows=rejected,columns=columns,issues=issues)
        except (ValueError,UnicodeError):
            return _unavailable(w,d,'SOURCE_OR_POLICY_UNSUPPORTED')
        finally: _compute.release()


def gold_assessment(w,d,upload,model,version,user,*,read_artifact=None):
    """Optional reader is a trusted server injection, never an HTTP argument.

    No runtime reader is bound in this phase. Disabled readers cannot trigger
    S3 access. Local tests inject versioned, checksum-verifying memory storage.
    """
    with database.repository_transaction() as conn:
        _scope(conn,w,d,user)
        source = _upload(conn,w,d,upload)
        config = row(conn,'''SELECT c.* FROM warehouse_model_configurations c
            JOIN warehouse_model_versions v USING(workspace_id,warehouse_model_id,warehouse_model_version_id)
            WHERE c.workspace_id=%s AND c.source_dataset_id=%s AND c.warehouse_model_id=%s
              AND c.warehouse_model_version_id=%s AND v.status='APPROVED' ''',(w,d,model,version))
        if config is None: raise PermissionError('Model unavailable')
        gold = row(conn,'''SELECT c.artifact_identities,i.input_contract FROM processing_stage_heads h
            JOIN processing_artifact_candidates c USING(candidate_id)
            JOIN processing_stage_receipts r USING(receipt_id)
            JOIN processing_stage_intents i ON i.intent_id=r.intent_id
            WHERE h.stage='GOLD' AND h.file_id=%s AND i.input_contract->>'dataset_id'=%s
              AND i.input_contract->>'upload_id'=%s AND r.candidate_id=c.candidate_id
              AND c.status='VERIFIED' AND c.verification_report IS NOT NULL''',(source['file_id'],str(d),str(upload)))
        if gold is None: return _unavailable(w,d,'VERIFIED_GOLD_REQUIRED')
        if 'base' not in gold['artifact_identities']: return _unavailable(w,d,'EMPTY_GOLD_RESULT')
        contract = gold['input_contract']
        from app.services.warehouse_modeling_service import rule_digest
        if (contract['dataset_version']!=config['source_dataset_version_id'] or contract['rule_version']!=config['source_rule_version']
            or rule_digest(contract['rules'])!=config['source_rule_sha256'] or not contract.get('load_policy')
            or contract['load_policy']['policy_id']!=config['source_policy_id']):
            return _unavailable(w,d,'MODEL_SOURCE_VERSION_DIFFERS')
        if read_artifact is None: return _unavailable(w,d,'TRUSTED_GOLD_READER_UNAVAILABLE')
        if not _compute.acquire(blocking=False): raise PreviewBusy()
        try:
            from app.schemas.warehouse_modeling import SourceSchema,ModelConfiguration
            from app.warehouse.gold_projection import assess_gold_projection
            from pydantic import TypeAdapter
            import pandas as pd
            import pyarrow.parquet as pq
            identity = ObjectIdentity.decode(gold['artifact_identities']['base'])
            if identity.size>MAX_PREVIEW_BYTES: return _unavailable(w,d,'PREVIEW_LIMIT_EXCEEDED')
            body = read_artifact(identity)
            if not isinstance(body,bytes) or len(body)!=identity.size or hashlib.sha256(body).hexdigest()!=identity.sha256: raise SourceMismatch()
            metadata = pq.read_metadata(io.BytesIO(body))
            if metadata.num_rows>1000 or metadata.num_columns>128: return _unavailable(w,d,'PREVIEW_LIMIT_EXCEEDED')
            dq = conn.execute('SELECT valid_rows FROM data_quality_runs WHERE dq_run_id=%s',(contract['dq_run'],)).fetchone()
            assessment = assess_gold_projection(pd.read_parquet(io.BytesIO(body)),TypeAdapter(ModelConfiguration).validate_python(config['configuration']),
                SourceSchema.model_validate(config['source_schema']),expected_rows=dq[0])
            return dict(workspace_id=w,dataset_id=d,status='ASSESSED',rows=assessment.rows,columns=list(assessment.columns))
        finally: _compute.release()
