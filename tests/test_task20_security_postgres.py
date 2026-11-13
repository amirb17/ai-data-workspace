"""Task 20 real transaction/registry faults, never AWS or persistent schemas."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
from dataclasses import asdict
from threading import Event
from uuid import uuid4

import pandas as pd
import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.db import database, processing_job_repository as jobs
from app.db import processing_integration_repository as registry
from app.processing_worker.integrated_executor import IntegratedExecutor
from app.processing_worker.verification_service import RegistryVerifier
from app.storage.versioned_artifacts import VersionedArtifacts, ObjectIdentity
from test_task20_processing_postgres import pipeline
from test_processing_jobs_postgres import source
from test_rule_approval_postgres import pg


@pytest.fixture
def registered(pipeline):
    src, client, _ = pipeline
    from app.config import S3_BUCKET_NAME
    store = VersionedArtifacts(client, S3_BUCKET_NAME)
    job = jobs.create_job(src[1], src[2], 'BRONZE', 'registry')
    with src[0]() as conn: conn.execute("SELECT task20_admit(%s,%s,'BRONZE','registry')",(src[1],src[2]))
    lease = jobs.claim_job_result(job['job_id']).lease
    obj = client.head_object(Bucket=store.bucket,Key='raw/task20.csv')
    raw = client.objects['raw/task20.csv',obj['VersionId']]
    import hashlib
    identity = ObjectIdentity('raw/task20.csv',obj['VersionId'],hashlib.sha256(raw).hexdigest(),len(raw))
    with database.repository_transaction() as conn:
        _, contract = registry.contract(conn,lease,source_identity=identity)
        attempt = conn.execute("""INSERT INTO processing_attempts(file_id,attempt_number,stage,status,started_at)
            VALUES(%s,1,'BRONZE','PROCESSING',clock_timestamp()) RETURNING attempt_id""", (job['file_id'],)).fetchone()[0]
        conn.execute("INSERT INTO processing_job_stage_attempts VALUES(%s,%s,'BRONZE',%s)", (lease.job_id,lease.lease_token,attempt))
    iid = uuid4()
    def candidate(expected=None):
        cid = uuid4()
        data = pd.read_csv(io.BytesIO(raw))
        data['_source_file_id'] = job['file_id']
        data['_bronze_ingested_at'] = pd.Timestamp('2026-01-01',tz='UTC')
        lexical = pd.read_csv(io.BytesIO(raw),dtype=str,keep_default_na=False,na_filter=False)
        objects = {}
        for role,frame in [('data',data),('lexical',lexical)]:
            output=io.BytesIO(); frame.to_parquet(output,index=False)
            objects[role]=asdict(store.put(f'artifact-candidates/{cid}/{role}.parquet',output.getvalue()))
        descriptors=[dict(role=r,key=o['key'],sha256=o['sha256'],size_bytes=o['size']) for r,o in objects.items()]
        manifest=json.dumps(dict(version=1,intent_id=str(iid),candidate_id=str(cid),stage='BRONZE',
            input_fingerprint=registry.fingerprint(contract),artifacts=descriptors)).encode()
        manifest=store.put(f'artifact-candidates/{cid}/manifest.json',manifest)
        with database.repository_transaction() as conn:
            if conn.execute('SELECT 1 FROM processing_stage_intents WHERE intent_id=%s',(iid,)).fetchone():
                conn.execute('''INSERT INTO processing_artifact_candidates(candidate_id,intent_id,manifest_sha256,manifest_identity,artifact_identities)
                    VALUES(%s,%s,%s,%s,%s)''',(cid,iid,manifest.sha256,Jsonb(asdict(manifest)),Jsonb(objects)))
            else:
                registry.register(conn,lease,contract,cid,manifest,objects,intent_id=iid,expected_receipt=expected)
        return cid,objects,manifest
    verifier=RegistryVerifier(store,src[0],isolated_test=True)
    return src,client,store,job,lease,contract,candidate,verifier,attempt


def publish(f,cid):
    with database.get_connection() as conn:
        return conn.execute('SELECT task20_publish(%s,%s,%s)',(f[4].job_id,f[4].lease_token,cid)).fetchone()[0]


@pytest.mark.parametrize('fault',['corrupt','missing','manifest','substitute_latest'])
def test_verifier_uses_registered_versions_and_rejects_damage(registered,fault):
    f=registered; cid,objects,manifest=f[6]()
    obj=objects['data']; key=(obj['key'],obj['version'])
    if fault=='substitute_latest':
        f[1].seed(obj['key'],b'wrong newest version')
        assert f[7].verify(cid)['artifacts']==objects
        return
    if fault=='missing': del f[1].objects[key]
    elif fault=='corrupt': f[1].objects[key]=b'x'*obj['size']
    else: f[1].objects[manifest.key,manifest.version]=b'{}'
    with pytest.raises((ValueError,KeyError)): f[7].verify(cid)
    with f[0][0]() as conn:
        assert conn.execute('SELECT status FROM processing_artifact_candidates WHERE candidate_id=%s',(cid,)).fetchone()[0]=='STAGED'
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==0


def test_generation_cas_and_atomic_outer_rollback(registered):
    f=registered
    first,*_=f[6](); stale,*_=f[6]()
    f[7].verify(first); f[7].verify(stale)
    with pytest.raises(RuntimeError,match='rollback'):
        with database.repository_transaction():
            publish(f,first)
            raise RuntimeError('rollback')
    with f[0][0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_heads').fetchone()[0]==0
    receipt=publish(f,first)
    assert publish(f,first)==receipt
    with pytest.raises(psycopg.DatabaseError,match='generation conflict'): publish(f,stale)
    with f[0][0]() as conn:
        assert conn.execute('SELECT generation FROM processing_stage_heads').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1


@pytest.mark.parametrize('change',['owner','source','expired','takeover'])
def test_publication_rechecks_current_authority(registered,change):
    f=registered; cid,*_=f[6](); f[7].verify(cid)
    with f[0][0]() as conn:
        if change=='owner': conn.execute("UPDATE datasets SET owner='dev:other'")
        elif change=='source': conn.execute("UPDATE physical_files SET file_hash=%s",('0'*64,))
        else: conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'")
    if change=='takeover':
        assert jobs.claim_job_result(f[4].job_id).lease.lease_token!=f[4].lease_token
    with pytest.raises(psycopg.DatabaseError): publish(f,cid)
    with f[0][0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_heads').fetchone()[0]==0


def test_lease_expiry_during_real_row_lock_wait(registered):
    f=registered; cid,*_=f[6](); f[7].verify(cid)
    with f[0][0]() as conn:
        conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()+interval '120 seconds'")
    # Preconnect both sessions, then verify the contender is actually waiting.
    blocker=f[0][0](); contender=f[0][0]()
    waiting=Event()
    try:
        blocker.execute('SELECT 1 FROM processing_jobs WHERE job_id=%s FOR UPDATE',(f[4].job_id,))
        pid=contender.info.backend_pid
        def run():
            waiting.set()
            try:
                contender.execute('SET LOCAL statement_timeout=20000')
                contender.execute('SELECT task20_publish(%s,%s,%s)',(f[4].job_id,f[4].lease_token,cid))
            finally: contender.rollback()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(run)
            try:
                assert waiting.wait(5)
                # Catalog lock observation, no arbitrary timing sleep.
                import time
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    if blocker.execute('SELECT cardinality(pg_blocking_pids(%s))>0',(pid,)).fetchone()[0]: break
                else: pytest.fail('Publication never reached the held job lock')
                blocker.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'")
            finally: blocker.commit()
            with pytest.raises(psycopg.DatabaseError,match='lease unavailable'): future.result(timeout=25)
    finally:
        blocker.close(); contender.close()
    with f[0][0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==0


def test_admission_outbox_and_send_response_loss(registered):
    from app.processing_worker.dispatch import admit,dispatch_one
    f=registered; src=f[0]
    # Clear the fixture's running job through supported terminal completion.
    jobs.finish_job(f[4].job_id,f[4].lease_token)
    job=admit(src[1],src[2],'BRONZE','outbox',isolated_test=True)
    assert admit(src[1],src[2],'BRONZE','outbox',isolated_test=True)==job
    class Client:
        messages=[]
        failed=True
        def send_message(self,**kwargs):
            self.messages.append(json.loads(kwargs['MessageBody']))
            if self.failed: raise TimeoutError('response lost after accepted send')
    client=Client()
    with pytest.raises(TimeoutError): dispatch_one(client,'offline',enabled=True,isolated_test=True)
    with src[0]() as conn:
        assert conn.execute('SELECT delivered_at FROM processing_dispatch_outbox WHERE job_id=%s',(job,)).fetchone()[0] is None
        conn.execute("UPDATE processing_dispatch_outbox SET claim_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job,))
    client.failed=False
    assert dispatch_one(client,'offline',enabled=True,isolated_test=True)
    assert client.messages==[{'job_id':str(job)}]*2


def test_admission_rollback_source_pin_immutability_and_default_security(registered,monkeypatch):
    from app.processing_worker.dispatch import admit
    from app.processing_worker.runtime_security import require_runtime
    f=registered; src=f[0]
    with pytest.raises(RuntimeError):
        with database.repository_transaction():
            admit(src[1],src[2],'BRONZE','rollback',isolated_test=True)
            raise RuntimeError('rollback')
    with src[0]() as conn:
        assert conn.execute("SELECT count(*) FROM processing_jobs WHERE idempotency_key='rollback'").fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM processing_dispatch_outbox').fetchone()[0]==1
        # PUBLIC has no SECURITY DEFINER execution grants.
        assert conn.execute("""SELECT count(*) FROM pg_proc p,aclexplode(p.proacl) a
            WHERE p.pronamespace=current_schema()::regnamespace AND p.proname LIKE 'task20_%'
            AND p.prosecdef AND a.grantee=0 AND a.privilege_type='EXECUTE'""").fetchone()[0]==0
        monkeypatch.delenv('DATARISE_PROCESSING_DEPLOYMENT_APPROVED',raising=False)
        with pytest.raises(PermissionError): require_runtime(conn,'worker')
    with pytest.raises(psycopg.DatabaseError):
        with src[0]() as conn: conn.execute("UPDATE processing_source_objects SET identity='{}'")


def test_dispatch_commits_claim_before_send_inside_outer_transaction(registered):
    from app.processing_worker.dispatch import admit,dispatch_one
    f=registered; src=f[0]
    jobs.finish_job(f[4].job_id,f[4].lease_token)
    job=admit(src[1],src[2],'BRONZE','outer-dispatch',isolated_test=True)
    observed=[]
    class Client:
        def send_message(self,**kwargs):
            assert json.loads(kwargs['MessageBody'])=={'job_id':str(job)}
            # Independent connection must see the committed claim BEFORE send.
            with src[0]() as conn:
                token,sent=conn.execute('SELECT claim_token,delivered_at FROM processing_dispatch_outbox WHERE job_id=%s',(job,)).fetchone()
            assert token is not None, 'Outbox claim was not committed before send'
            assert sent is None
            observed.append(token)
    with pytest.raises(RuntimeError,match='outer rollback'):
        with database.repository_transaction() as conn:
            conn.execute('SELECT 1')
            assert dispatch_one(Client(),'offline',enabled=True,isolated_test=True)
            raise RuntimeError('outer rollback')
    assert len(observed)==1
    with src[0]() as conn:
        assert conn.execute('SELECT delivered_at IS NOT NULL FROM processing_dispatch_outbox WHERE job_id=%s',(job,)).fetchone()[0]


def test_mapped_bronze_interruption_recovery_fences_old_attempt(registered):
    from app.processing_worker.boundary import ExecutionBoundary
    f=registered
    with f[0][0]() as conn: conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'")
    lease=jobs.claim_job_result(f[4].job_id).lease
    adapter=IntegratedExecutor(None,None,isolated_test=True)
    boundary=ExecutionBoundary(lease,lambda:3600000)
    with adapter._physical_stage(boundary):
        with f[0][0]() as conn:
            assert conn.execute('SELECT status FROM processing_attempts WHERE attempt_id=%s',(f[8],)).fetchone()[0]=='CRASHED'
    with pytest.raises(psycopg.DatabaseError,match='immutable'):
        with f[0][0]() as conn: conn.execute("UPDATE processing_attempts SET status='SUCCESS' WHERE attempt_id=%s",(f[8],))


def test_durable_upload_completion_and_source_pin_rollback_together(pg,monkeypatch):
    from app.services import upload_session_service as uploads
    from app.processing_worker import runtime_security
    from app.storage import s3_service
    from app.storage.versioned_artifacts import MemoryVersionedClient
    from app.processing_worker.dispatch import admit
    connect,user,ws,ds,*_=pg
    client=MemoryVersionedClient(); payload=b'id,city\n1,A\n'
    monkeypatch.setenv('DATARISE_PROCESSING_DEPLOYMENT_APPROVED','true')
    original=runtime_security.require_runtime
    monkeypatch.setattr(runtime_security,'require_runtime',lambda conn,purpose,**kw:original(conn,purpose,isolated_test=True))
    monkeypatch.setattr(uploads,'generate_presigned_upload_url',lambda **kw:'offline-signed-url')
    monkeypatch.setattr(uploads,'get_object_metadata',lambda key:{'size':len(payload),'content_type':'text/csv'})
    monkeypatch.setattr(s3_service,'get_s3_client',lambda:client)
    # MemoryVersionedClient records version/size, but not S3 MIME metadata.
    # Supply the ContentType from the upload fixture without weakening the
    # production read_staged() check for a version-pinned CSV object.
    head_object = client.head_object
    def csv_head_object(*, Bucket, Key):
        metadata = head_object(Bucket=Bucket, Key=Key)
        if Key.startswith('staging/'):
            metadata['ContentType'] = 'text/csv'
        return metadata
    monkeypatch.setattr(client,'head_object',csv_head_object)
    session=uploads.initiate_upload_session(user['user_id'],ws,ds,'source.csv',len(payload),content_type='text/csv')
    client.seed(session['object_key'],payload)
    complete=uploads.complete_session
    def fail(*args):
        complete(*args)
        raise RuntimeError('SQL publication interrupted')
    monkeypatch.setattr(uploads,'complete_session',fail)
    with pytest.raises(RuntimeError,match='interrupted'): uploads.complete_upload_session(session['upload_request_id'],user['user_id'])
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM processing_source_objects').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM physical_files WHERE file_size=%s',(len(payload),)).fetchone()[0]==0
    monkeypatch.setattr(uploads,'complete_session',complete)
    result=uploads.complete_upload_session(session['upload_request_id'],user['user_id'])
    assert uploads.complete_upload_session(session['upload_request_id'],user['user_id'])==result
    job=admit(user['user_id'],session['upload_request_id'],'BRONZE','completed-upload',isolated_test=True)
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM processing_source_objects').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM processing_dispatch_outbox WHERE job_id=%s',(job,)).fetchone()[0]==1


def test_outbox_tracks_renewal_and_durable_retry_without_early_dispatch(registered):
    from app.processing_worker.dispatch import dispatch_one
    f=registered; lease=f[4]
    renewed=jobs.renew_job_lease(lease.job_id,lease.lease_token,lease_seconds=240)
    with f[0][0]() as conn:
        assert conn.execute('SELECT available_at FROM processing_dispatch_outbox WHERE job_id=%s',(lease.job_id,)).fetchone()[0]==renewed.lease_expires_at
    result=jobs.finish_job(lease.job_id,lease.lease_token,failure_code='PROCESSING_FAILED',retry=True,retry_delay_seconds=30)
    assert result['status']=='RETRY_WAIT'
    with f[0][0]() as conn:
        assert conn.execute('SELECT available_at FROM processing_dispatch_outbox WHERE job_id=%s',(lease.job_id,)).fetchone()[0]==result['available_at']
    class Client:
        def send_message(self,**kwargs): pytest.fail('Not-due retry dispatched early')
    assert not dispatch_one(Client(),'offline',enabled=True,isolated_test=True)
    with pytest.raises(psycopg.DatabaseError,match='identity is immutable'):
        with f[0][0]() as conn: conn.execute("UPDATE processing_jobs SET operation='GOLD' WHERE job_id=%s",(lease.job_id,))


@pytest.mark.parametrize('fault',['untrusted-source','expired-lease','foreign-intent-file'])
def test_verifier_authorizes_inputs_before_any_storage_access(registered,fault):
    f=registered; cid,iid=uuid4(),uuid4(); contract=json.loads(json.dumps(f[5]))
    if fault=='untrusted-source': contract['source']['key']='raw/not-authorized.csv'
    manifest=ObjectIdentity(f'artifact-candidates/{cid}/manifest.json','not-created','0'*64,10)
    objects={'data':asdict(ObjectIdentity(f'artifact-candidates/{cid}/data.parquet','not-created','0'*64,10))}
    with f[0][0]() as conn:
        file_id=f[3]['file_id']
        if fault=='foreign-intent-file':
            file_id=conn.execute("INSERT INTO physical_files(file_name,file_size,file_hash,storage_path,status) VALUES('other.csv',10,%s,'test-only','UPLOADED') RETURNING file_id",('f'*64,)).fetchone()[0]
        conn.execute("""INSERT INTO processing_stage_intents(intent_id,job_id,lease_token,stage,file_id,input_fingerprint,input_contract)
            VALUES(%s,%s,%s,'BRONZE',%s,%s,%s)""",(iid,f[4].job_id,f[4].lease_token,file_id,registry.fingerprint(contract),Jsonb(contract)))
        conn.execute('''INSERT INTO processing_artifact_candidates(candidate_id,intent_id,manifest_sha256,manifest_identity,artifact_identities)
            VALUES(%s,%s,%s,%s,%s)''',(cid,iid,manifest.sha256,Jsonb(asdict(manifest)),Jsonb(objects)))
        if fault=='expired-lease': conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'")
    before=list(f[1].calls)
    with pytest.raises((psycopg.DatabaseError,ValueError)): f[7].verify(cid)
    assert f[1].calls==before
    if fault=='foreign-intent-file':
        report=dict(manifest_sha256=manifest.sha256,manifest_identity=asdict(manifest),artifacts=objects)
        with pytest.raises(psycopg.DatabaseError,match='evidence mismatch'):
            with f[0][0]() as conn: conn.execute('SELECT task20_verify(%s,%s,%s,%s)',(f[4].job_id,f[4].lease_token,cid,Jsonb(report)))


def test_successful_callback_without_trusted_verification_cannot_publish(pipeline):
    src,client,run=pipeline
    run.adapter.verifier=lambda candidate:None
    with pytest.raises(RuntimeError,match='Verified current candidate required'): run('BRONZE','untrusted-callback')
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM processing_stage_heads').fetchone()[0]==0
        assert conn.execute('SELECT schema_hash FROM physical_files').fetchone()[0] is None
        assert conn.execute('SELECT count(*) FROM dataset_profiles').fetchone()[0]==0
        assert conn.execute("SELECT status FROM processing_jobs").fetchone()[0]=='RETRY_WAIT'


def test_unselected_policy_cannot_authorize_incremental_publication(pipeline):
    from app.config import S3_BUCKET_NAME
    from app.processing_worker.boundary import ExecutionBoundary
    from app.processing_worker.dispatch import admit
    from app.processing_worker.incremental_adapter import ApplyGuard
    from app.processing_worker import io_context
    from app.schemas.incremental import LoadPolicyRequest
    from app.services.incremental_policy_service import save_policy
    from app.services.append_application_service import apply_incremental
    from test_task20_processing_postgres import approve
    src,client,run=pipeline
    run('BRONZE'); association=approve(src)
    job=admit(src[1],src[2],'SILVER','no-selected-policy',isolated_test=True)
    lease=jobs.claim_job_result(job).lease
    boundary=ExecutionBoundary(lease,lambda:3600000)
    run.adapter(boundary)  # Publish real Silver, retaining the running job lease.
    with src[0]() as conn:
        contract=conn.execute('SELECT input_contract FROM processing_job_inputs WHERE job_id=%s',(job,)).fetchone()[0]
        assert contract['load_policy'] is None
        silver=registry.head(conn,'SILVER',contract['file_id'],association)
        ws,ds=conn.execute('SELECT workspace_id,dataset_id FROM upload_requests WHERE upload_id=%s',(src[2],)).fetchone()
    user={'user_id':src[1],'owner_key':'dev:test'}
    # Another authorized client can prepare a policy after this job was pinned.
    save_policy(ws,ds,user,LoadPolicyRequest(dataset_version_id=contract['dataset_version'],
        expected_policy_version=0,load_strategy='APPEND',business_keys=[],schema_evolution_policy='STRICT'))
    store=VersionedArtifacts(client,S3_BUCKET_NAME,checkpoint=lambda:boundary.checkpoint(renew=True))
    guard=ApplyGuard(boundary,store,contract,ObjectIdentity.decode(silver['artifact_identities']['data']))
    # A miswired executor must not turn that old job into authority for a new policy.
    with io_context.using(guard), pytest.raises(RuntimeError,match='previous trusted state'):
        apply_incremental(ws,ds,user,src[2])
    with src[0]() as conn:
        assert conn.execute('SELECT current_state_id FROM datasets WHERE dataset_id=%s',(ds,)).fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM dataset_state_versions WHERE status='PUBLISHED'").fetchone()[0]==0
        assert conn.execute('SELECT status FROM delivery_applications').fetchone()[0]=='FAILED'


@pytest.mark.parametrize('nested',[False,True])
def test_independent_savepoint_rolls_back_nested_repository_writes(source,nested):
    from contextlib import nullcontext
    with source[0]() as conn:
        before=conn.execute('SELECT display_name FROM users WHERE user_id=%s',(source[1],)).fetchone()[0]
    with pytest.raises(RuntimeError,match='savepoint rollback'):
        with (database.repository_transaction() if nested else nullcontext()):
            with database.repository_savepoint():
                with database.get_connection() as conn:
                    conn.execute("UPDATE users SET display_name='must roll back' WHERE user_id=%s",(source[1],))
                raise RuntimeError('savepoint rollback')
    with source[0]() as conn:
        assert conn.execute('SELECT display_name FROM users WHERE user_id=%s',(source[1],)).fetchone()[0]==before


def test_savepoint_inner_failure_preserves_and_commits_outer_writes(source):
    with database.repository_transaction() as conn:
        conn.execute("UPDATE users SET display_name='outer write' WHERE user_id=%s",(source[1],))
        with pytest.raises(RuntimeError,match='inner rollback'):
            with database.repository_savepoint():
                with database.get_connection() as nested:
                    nested.execute("UPDATE users SET display_name='inner write' WHERE user_id=%s",(source[1],))
                raise RuntimeError('inner rollback')
        assert conn.execute('SELECT display_name FROM users WHERE user_id=%s',(source[1],)).fetchone()[0]=='outer write'
    with source[0]() as conn:
        assert conn.execute('SELECT display_name FROM users WHERE user_id=%s',(source[1],)).fetchone()[0]=='outer write'


def test_migration_reapplication_is_rejected_without_partial_change_and_deployment_dependencies_exist(source):
    import re
    from pathlib import Path
    migration=Path('migrations/0043_processing_integration.sql').read_text(encoding='utf-8')
    deployment=Path('deployment/task20_runtime_privileges.sql').read_text(encoding='utf-8')
    with source[0]() as conn:
        schema=conn.execute('SELECT current_schema()').fetchone()[0]
        before=conn.execute('SELECT count(*) FROM pg_class WHERE relnamespace=current_schema()::regnamespace').fetchone()[0]
        with pytest.raises(psycopg.errors.DuplicateColumn):
            with conn.transaction(): conn.execute(migration)
        assert conn.execute('SELECT count(*) FROM pg_class WHERE relnamespace=current_schema()::regnamespace').fetchone()[0]==before
        signatures=re.findall(r'ALTER FUNCTION\s+(task20_\w+\([^)]*\))',deployment)
        assert signatures
        for signature in signatures:
            assert conn.execute('SELECT to_regprocedure(%s)',(signature,)).fetchone()[0] is not None
        functions=conn.execute("""SELECT p.oid::regprocedure::text,p.proconfig,
            EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
            FROM pg_proc p WHERE p.pronamespace=current_schema()::regnamespace AND p.prosecdef
            AND p.proname LIKE 'task20_%'""").fetchall()
        assert functions
        normalized={s.lower().replace(' ','') for s in signatures}
        for signature,config,public_execute in functions:
            assert signature.lower().replace(' ','') in normalized
            assert not public_execute
            assert f'search_path=pg_catalog, {schema}, pg_temp' in config
    assert 'ON ALL SEQUENCES' not in deployment
    assert 'snapshot_delivery_contexts TO' in deployment
    # Compile/run only the read-only identity preflight. No GRANT or role DDL.
    preflight=re.search(r'DO \$\$.*?END \$\$;',deployment,re.S).group(0)
    assert 'GRANT' not in preflight and 'ALTER ROLE' not in preflight
    with source[0]() as conn:
        for purpose in ('authority','api','worker','verifier','dispatch'):
            conn.execute('SELECT set_config(%s,%s,true)',('datarise_task20.'+purpose,'missing_task20_'+purpose))
        with pytest.raises(psycopg.DatabaseError,match='existing unprivileged identities'):
            with conn.transaction(): conn.execute(preflight)
