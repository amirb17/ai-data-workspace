"""Version-bound extension of the existing publication contract; no stage algorithms."""
import hashlib
import json
from dataclasses import asdict

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.db import database, processing_job_repository as jobs
from app.db import artifact_publication_repository as publication


def fingerprint(contract):
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def row(conn, query, args=()):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query, args).fetchone()


def head(conn, stage, file_id, association=None):
    return row(conn, """SELECT h.*,i.input_contract,i.input_fingerprint,c.manifest_identity,c.artifact_identities,
        c.verification_report FROM processing_stage_heads h
        JOIN processing_stage_receipts r ON r.receipt_id=h.receipt_id
        JOIN processing_stage_intents i ON i.intent_id=r.intent_id
        JOIN processing_artifact_candidates c ON c.candidate_id=h.candidate_id
        WHERE h.stage=%s AND h.file_id=%s AND h.dataset_version_file_id IS NOT DISTINCT FROM %s""",
        (stage, file_id, association))


def authorized_job(conn, lease):
    publication._require_active_job_lease(conn, lease.job_id, lease.lease_token)
    job = row(conn, 'SELECT * FROM processing_jobs WHERE job_id=%s', (lease.job_id,))
    publication._require_current_job_source(conn, job)
    return job


def contract(conn, lease, *, source_identity=None):
    job = authorized_job(conn, lease)
    stage = job['operation']
    if stage not in {'BRONZE', 'SILVER', 'GOLD'}:
        raise ValueError('CONTINUE is not a bounded stage job')
    result = {'version': 1, 'stage': stage, 'file_id': job['file_id'], 'dataset_id': job['dataset_id'],
              'upload_id': job['upload_id']}
    if stage == 'BRONZE':
        stored = row(conn, 'SELECT identity FROM processing_source_objects WHERE file_id=%s', (job['file_id'],))
        if stored is None:
            if source_identity is None:
                raise ValueError('Registered versioned Raw source required')
            conn.execute('INSERT INTO processing_source_objects(file_id,identity) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                         (job['file_id'], Jsonb(asdict(source_identity))))
            stored = row(conn, 'SELECT identity FROM processing_source_objects WHERE file_id=%s', (job['file_id'],))
        result['source'] = stored['identity']
    else:
        association = row(conn, """SELECT f.dataset_version_file_id,f.applied_rule_version,dv.dataset_version_id,p.rules
            FROM dataset_version_files f JOIN dataset_versions dv ON dv.dataset_version_id=f.dataset_version_id
            JOIN physical_files pf ON pf.file_id=f.file_id
            JOIN approved_rule_policies p ON p.dataset_version_id=dv.dataset_version_id AND p.rule_version=f.applied_rule_version
            WHERE f.file_id=%s AND dv.dataset_id=%s AND dv.schema_hash=pf.schema_hash""", (job['file_id'], job['dataset_id']))
        if not association or not association['rules']:
            raise ValueError('Approved pinned rules required')
        aid = association['dataset_version_file_id']
        upstream = head(conn, 'BRONZE' if stage == 'SILVER' else 'SILVER', job['file_id'], None if stage == 'SILVER' else aid)
        if upstream is None or upstream['verification_report'] is None:
            raise ValueError('Verified upstream stage required; legacy adoption is not implicit')
        result.update(association=aid, rule_version=association['applied_rule_version'],
            dataset_version=association['dataset_version_id'], rules=association['rules'],
            upstream_receipt=str(upstream['receipt_id']), upstream_manifest=upstream['manifest_identity'],
            upstream_artifacts=upstream['artifact_identities'])
        selected = row(conn, """SELECT to_jsonb(p) AS policy FROM dataset_load_policies p WHERE p.dataset_id=%s
            AND p.policy_id=coalesce((SELECT policy_id FROM delivery_applications WHERE upload_request_id=%s),
              (SELECT policy_id FROM dataset_load_policies WHERE dataset_id=%s ORDER BY policy_version DESC LIMIT 1))""",
            (job['dataset_id'], job['upload_id'], job['dataset_id']))
        result['load_policy'] = selected['policy'] if selected else None
        result['snapshot_context'] = None
        if result['load_policy'] and result['load_policy']['load_strategy'] == 'SNAPSHOT':
            declared = conn.execute('SELECT to_jsonb(s) FROM snapshot_delivery_contexts s WHERE upload_request_id=%s',
                                    (job['upload_id'],)).fetchone()
            if not declared:
                raise ValueError('Snapshot declaration required before execution')
            result['snapshot_context'] = declared[0]
        if stage == 'SILVER':
            result['raw_source'] = row(conn, 'SELECT identity FROM processing_source_objects WHERE file_id=%s', (job['file_id'],))['identity']
        if stage == 'GOLD':
            result['applied_state'] = conn.execute('SELECT task20_applied_state(%s,%s,%s)',
                (job['dataset_id'],job['upload_id'],result['load_policy']['policy_id'] if result['load_policy'] else None)).fetchone()[0]
            result['dependency_epoch'] = conn.execute('SELECT revision FROM processing_dependency_epochs WHERE dataset_id=%s', (job['dataset_id'],)).fetchone()[0]
            dq = row(conn, """SELECT dq.* FROM data_quality_runs dq JOIN processing_attempts a ON a.attempt_id=dq.attempt_id
                WHERE dq.dataset_version_file_id=%s AND dq.rule_version=%s AND a.status='SUCCESS'
                ORDER BY dq.dq_run_id DESC LIMIT 1""", (aid, result['rule_version']))
            if dq is None:
                raise ValueError('Committed Silver DQ required')
            result['dq_run'] = dq['dq_run_id']
            result['profiles'] = conn.execute("SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY column_name),'[]'::jsonb) FROM dataset_profiles p WHERE file_id=%s", (job['file_id'],)).fetchone()[0]
            result['models'] = conn.execute("SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY warehouse_model_version_id),'[]'::jsonb) FROM warehouse_model_configurations m WHERE source_dataset_id=%s", (job['dataset_id'],)).fetchone()[0]
    conn.execute('INSERT INTO processing_job_inputs(job_id,input_contract,input_fingerprint) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
        (lease.job_id, Jsonb(result), fingerprint(result)))
    conn.execute('SELECT task20_require_input(%s,%s,%s)', (lease.job_id, lease.lease_token, Jsonb(result)))
    return job, result


def register(conn, lease, value, candidate_id, manifest, identities, *, intent_id, expected_receipt):
    job = authorized_job(conn, lease)
    conn.execute('SELECT task20_require_input(%s,%s,%s)', (lease.job_id, lease.lease_token, Jsonb(value)))
    conn.execute("""INSERT INTO processing_stage_intents(intent_id,job_id,lease_token,stage,file_id,
        dataset_version_file_id,input_fingerprint,input_contract,expected_receipt_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (intent_id, lease.job_id, lease.lease_token, value['stage'], job['file_id'], value.get('association'),
         fingerprint(value), Jsonb(value), expected_receipt))
    conn.execute("""INSERT INTO processing_artifact_candidates(candidate_id,intent_id,manifest_sha256,
        manifest_identity,artifact_identities) VALUES(%s,%s,%s,%s,%s)""",
        (candidate_id, intent_id, manifest.sha256, Jsonb(asdict(manifest)), Jsonb(identities)))
    return intent_id


def registry(conn, candidate_id):
    return row(conn, """SELECT c.*,i.job_id,i.lease_token,i.stage,i.file_id,i.dataset_version_file_id,
        i.input_contract,i.input_fingerprint FROM processing_artifact_candidates c
        JOIN processing_stage_intents i ON i.intent_id=c.intent_id WHERE c.candidate_id=%s""", (candidate_id,))
