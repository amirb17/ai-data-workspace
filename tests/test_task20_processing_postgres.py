"""Real existing stage algorithms + isolated PG + versioned in-memory S3 only."""
import hashlib

import pytest

from app.db import database, processing_job_repository as jobs
from app.processing_worker import execution
from app.processing_worker.integrated_executor import IntegratedExecutor
from app.processing_worker.verification_service import RegistryVerifier
from app.storage.versioned_artifacts import MemoryVersionedClient, VersionedArtifacts
from test_rule_approval_postgres import pg
from test_processing_jobs_postgres import source


@pytest.fixture
def pipeline(source):
    from app.config import S3_BUCKET_NAME
    connect, user_id, upload_id = source
    client = MemoryVersionedClient()
    raw = b'id,city,amount\n1,A,10\n2,A,20\n'
    identity = client.seed('raw/task20.csv', raw)
    with connect() as conn:
        conn.execute("""UPDATE physical_files SET file_hash=%s,file_size=%s,storage_path=%s,schema_hash=NULL""",
                     (hashlib.sha256(raw).hexdigest(), len(raw), 's3://'+S3_BUCKET_NAME+'/raw/task20.csv'))
        from dataclasses import asdict
        from psycopg.types.json import Jsonb
        conn.execute('SELECT task20_register_source(%s,%s,%s)',(user_id,upload_id,Jsonb(asdict(identity))))
    verifier = RegistryVerifier(VersionedArtifacts(client, S3_BUCKET_NAME), connect, isolated_test=True)
    adapter = IntegratedExecutor(lambda check: VersionedArtifacts(client, S3_BUCKET_NAME, checkpoint=check),
                                  verifier.verify, enabled=True, isolated_test=True)
    def run(stage, key=None, *, upload=None):
        from app.processing_worker.dispatch import admit
        from app.db.processing_integration_repository import row
        job_id=admit(user_id,upload_id if upload is None else upload,stage,key or stage,isolated_test=True)
        with connect() as conn: job=row(conn,'SELECT * FROM processing_jobs WHERE job_id=%s',(job_id,))
        execution.execute_job(str(job['job_id']), boundary_executor=adapter, remaining_time_ms=lambda: 3600000)
        return job
    run.adapter = adapter
    return source, client, run


def approve(source):
    from app.services import processing_context_service as context, business_rule_service as rules
    from app.schemas.business_rules import BusinessRuleAnswer
    connect, user_id, upload_id = source
    with connect() as conn:
        association, ws, ds = conn.execute("""SELECT f.dataset_version_file_id,d.workspace_id,d.dataset_id
            FROM dataset_version_files f JOIN dataset_versions v ON v.dataset_version_id=f.dataset_version_id
            JOIN physical_files p ON p.file_id=f.file_id AND p.schema_hash=v.schema_hash
            JOIN datasets d ON d.dataset_id=v.dataset_id""").fetchone()
    user = {'user_id':user_id, 'owner_key':'dev:test'}
    with context.rule_mutation(association, user, 0, ws, ds):
        rules.submit_business_rule_answers(association, [BusinessRuleAnswer(column_name='id',rule_type='NOT_NULL',answer='YES')])
    with context.rule_mutation(association, user, 1, ws, ds):
        assert rules.finalize_business_rules(association)['finalized']
    return association


def test_existing_bronze_silver_gold_algorithms_publish_immutable_receipts(pipeline):
    source, client, run = pipeline
    bronze = run('BRONZE')
    association = approve(source)
    silver = run('SILVER')
    gold = run('GOLD')
    with source[0]() as conn:
        assert conn.execute("SELECT status FROM dataset_version_files WHERE dataset_version_file_id=%s", (association,)).fetchone()[0] == 'SUCCESS'
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM processing_jobs WHERE status='SUCCEEDED'").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM processing_attempts WHERE status='SUCCESS'").fetchone()[0] == 3
        assert conn.execute('SELECT total_rows,valid_rows,rejected_rows FROM data_quality_runs').fetchone() == (2,2,0)
        assert conn.execute("SELECT count(*) FROM gold_artifacts WHERE storage_path LIKE 'artifact-candidates/%'").fetchone()[0] >= 1
    assert all(key.startswith(('raw/', 'artifact-candidates/')) for key in client.latest)
    written = len([c for c in client.calls if c[0]=='put'])
    run('GOLD', 'completion-reconciliation')
    assert len([c for c in client.calls if c[0]=='put']) == written
    from app.processing_worker.dispatch import admit
    admit(source[1],source[2],'GOLD','stale-planner',isolated_test=True)
    # A legitimate changed planner dependency advances, never overwrites, history.
    with source[0]() as conn:
        conn.execute("UPDATE dataset_profiles SET negative_count=negative_count+1 WHERE column_name='amount'")
    import psycopg
    with pytest.raises(psycopg.DatabaseError,match='Admission input version changed'):
        run('GOLD','stale-planner')
    run('GOLD','new-planner-generation')
    with source[0]() as conn:
        assert conn.execute("SELECT generation FROM processing_stage_heads WHERE stage='GOLD'").fetchone()[0]==2
        assert conn.execute("SELECT count(*) FROM processing_stage_receipts WHERE stage='GOLD'").fetchone()[0]==2
        assert conn.execute("""SELECT g.gold_path=c.artifact_identities->'base'->>'key'
            FROM gold_runs g JOIN processing_stage_heads h ON h.dataset_version_file_id=g.dataset_version_file_id
            JOIN processing_artifact_candidates c ON c.candidate_id=h.candidate_id WHERE h.stage='GOLD'""").fetchone()[0]


def test_selected_append_policy_publishes_existing_dataset_state(pipeline):
    from app.schemas.incremental import LoadPolicyRequest
    from app.services.incremental_policy_service import save_policy
    source,client,run=pipeline
    run('BRONZE'); association=approve(source)
    with source[0]() as conn:
        ws,ds,version=conn.execute('SELECT d.workspace_id,d.dataset_id,v.dataset_version_id FROM datasets d JOIN dataset_versions v USING(dataset_id) JOIN dataset_version_files f USING(dataset_version_id) WHERE f.dataset_version_file_id=%s',(association,)).fetchone()
    save_policy(ws,ds,{'user_id':source[1],'owner_key':'dev:test'},LoadPolicyRequest(
        dataset_version_id=version,expected_policy_version=0,load_strategy='APPEND',business_keys=[],schema_evolution_policy='STRICT'))
    run('SILVER')
    with source[0]() as conn:
        assert conn.execute("SELECT a.status,s.status,s.row_count,d.current_state_id=s.state_id FROM delivery_applications a JOIN dataset_state_versions s ON s.state_id=a.result_state_id JOIN datasets d ON d.dataset_id=a.dataset_id").fetchone()==('SUCCESS','PUBLISHED',2,True)
    before=len(client.latest)
    run('SILVER','reconcile-applied'); assert len(client.latest)==before
    run('GOLD')


@pytest.mark.parametrize('crash_at',['write','verification','completion'])
def test_actual_bronze_crash_reclaim_and_completion_loss(pipeline,monkeypatch,crash_at):
    source,client,run=pipeline
    class ProcessCrash(BaseException): pass
    original_put=client.put_object
    original_verify=run.adapter.verifier
    original_finish=jobs.finish_job
    def crash(*args,**kwargs): raise ProcessCrash('simulated process interruption')
    if crash_at=='write': monkeypatch.setattr(client,'put_object',crash)
    elif crash_at=='verification': run.adapter.verifier=crash
    else: monkeypatch.setattr(jobs,'finish_job',crash)
    with pytest.raises(ProcessCrash): run('BRONZE','interrupted')
    with source[0]() as conn:
        receipts=conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]
        assert receipts==(1 if crash_at=='completion' else 0)
        assert conn.execute("SELECT status FROM processing_jobs").fetchone()[0]=='RUNNING'
        conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'")
    before=len([c for c in client.calls if c[0]=='put'])
    monkeypatch.setattr(client,'put_object',original_put)
    run.adapter.verifier=original_verify
    monkeypatch.setattr(jobs,'finish_job',original_finish)
    run('BRONZE','interrupted')
    with source[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1
        assert conn.execute("SELECT status,attempt_count FROM processing_jobs").fetchone()==('SUCCEEDED',2)
        if crash_at!='completion':
            assert conn.execute("SELECT status FROM processing_attempts ORDER BY attempt_number").fetchall()==[('CRASHED',),('SUCCESS',)]
    if crash_at=='completion': assert len([c for c in client.calls if c[0]=='put'])==before


def test_real_workers_overlap_without_duplicate_stage_attempts(pipeline):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.processing_worker.dispatch import admit
    source,client,run=pipeline
    first=admit(source[1],source[2],'BRONZE','worker-one',isolated_test=True)
    second=admit(source[1],source[2],'BRONZE','worker-two',isolated_test=True)
    entered,release=Event(),Event()
    verify=run.adapter.verifier
    def held(candidate):
        entered.set()
        if not release.wait(120): raise TimeoutError('Test did not release worker')
        return verify(candidate)
    run.adapter.verifier=held
    with ThreadPoolExecutor(max_workers=1) as pool:
        active=pool.submit(execution.execute_job,str(first),boundary_executor=run.adapter,remaining_time_ms=lambda:3600000)
        try:
            assert entered.wait(120)
            with pytest.raises(RuntimeError,match='Dataset processing is already active'):
                execution.execute_job(str(second),boundary_executor=run.adapter,remaining_time_ms=lambda:3600000)
            with source[0]() as conn:
                assert conn.execute('SELECT count(*) FROM processing_attempts').fetchone()[0]==1
                assert conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s',(first,)).fetchone()[0]=='RUNNING'
                assert conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s',(second,)).fetchone()[0]=='RETRY_WAIT'
        finally: release.set()
        active.result(timeout=120)
    with source[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1


def test_approval_revocation_after_admission_prevents_silver_execution(pipeline):
    from app.processing_worker.dispatch import admit
    source,client,run=pipeline
    run('BRONZE'); association=approve(source)
    admit(source[1],source[2],'SILVER','revoked',isolated_test=True)
    written=len(client.latest)
    with source[0]() as conn:
        conn.execute('UPDATE dataset_version_files SET applied_rule_version=NULL WHERE dataset_version_file_id=%s',(association,))
    with pytest.raises(ValueError,match='Approved pinned rules required'): run('SILVER','revoked')
    assert len(client.latest)==written
    with source[0]() as conn:
        assert conn.execute("SELECT count(*) FROM processing_attempts WHERE stage='SILVER'").fetchone()[0]==0


def new_delivery(pipeline, payload=None, *, reuse_file=None):
    """Completed authorized source in the fixture schema; bytes stay in memory."""
    from dataclasses import asdict
    from uuid import uuid4
    from psycopg.types.json import Jsonb
    from app.config import S3_BUCKET_NAME
    source,client,_=pipeline
    with source[0]() as conn:
        ws,ds=conn.execute('SELECT workspace_id,dataset_id FROM upload_requests WHERE upload_id=%s',(source[2],)).fetchone()
        if reuse_file is None:
            key=f'raw/{uuid4()}.csv'
            identity=client.seed(key,payload)
            file_id=conn.execute('''INSERT INTO physical_files(file_name,file_size,file_hash,storage_path,status)
                VALUES('next.csv',%s,%s,%s,'UPLOADED') RETURNING file_id''',
                (len(payload),hashlib.sha256(payload).hexdigest(),'s3://'+S3_BUCKET_NAME+'/'+key)).fetchone()[0]
        else:
            file_id=reuse_file
        upload=conn.execute("INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) VALUES(%s,%s,%s,%s,'UPLOADED') RETURNING upload_id",
            (source[1],ws,ds,file_id)).fetchone()[0]
        if reuse_file is None:
            conn.execute('SELECT task20_register_source(%s,%s,%s)',(source[1],upload,Jsonb(asdict(identity))))
    return upload,file_id


def state_evidence(pipeline):
    import io,json
    import pandas as pd
    source,client,_=pipeline
    with source[0]() as conn:
        state=conn.execute('''SELECT s.state_id,s.manifest_key,s.manifest_sha256 FROM datasets d
            JOIN dataset_state_versions s ON s.state_id=d.current_state_id''').fetchone()
    payload=client.objects[state[1],client.latest[state[1]]]
    assert hashlib.sha256(payload).hexdigest()==state[2]
    manifest=json.loads(payload)
    data=client.objects[manifest['data_key'],client.latest[manifest['data_key']]]
    assert hashlib.sha256(data).hexdigest()==manifest['data_sha256']
    return state[0],payload,pd.read_parquet(io.BytesIO(data))


@pytest.mark.parametrize('mode',['UPSERT','SNAPSHOT'])
def test_new_path_keyed_application_idempotency_contention_failure_and_retry(pipeline,monkeypatch,mode):
    """Actual stage engines and SQL CAS; inject only an interrupted publication."""
    import psycopg
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.schemas.incremental import LoadPolicyRequest,SnapshotDeliveryRequest
    from app.services.incremental_policy_service import save_policy
    from app.services.snapshot_context_service import declare_snapshot
    from app.processing_worker.dispatch import admit
    from app.processing_worker.incremental_adapter import ApplyGuard
    source,client,run=pipeline
    run('BRONZE'); association=approve(source)
    user={'user_id':source[1],'owner_key':'dev:test'}
    with source[0]() as conn:
        ws,ds,version=conn.execute('''SELECT d.workspace_id,d.dataset_id,v.dataset_version_id FROM datasets d
            JOIN dataset_versions v USING(dataset_id) JOIN dataset_version_files f USING(dataset_version_id)
            WHERE f.dataset_version_file_id=%s''',(association,)).fetchone()
    save_policy(ws,ds,user,LoadPolicyRequest(dataset_version_id=version,expected_policy_version=0,
        load_strategy=mode,business_keys=['id'],schema_evolution_policy='STRICT',
        snapshot_coverage='COMPLETE' if mode=='SNAPSHOT' else None))
    if mode=='SNAPSHOT':
        with pytest.raises(psycopg.DatabaseError,match='Snapshot declaration required'):
            admit(source[1],source[2],'SILVER','undeclared',isolated_test=True)
        declare_snapshot(ws,ds,user,source[2],SnapshotDeliveryRequest(coverage='COMPLETE',effective_at='2026-10-01T00:00:00Z'))
    run('SILVER','first')
    first_head,first_manifest,first_frame=state_evidence(pipeline)
    assert first_frame['id'].tolist()==[1,2]
    writes=len(client.calls)
    run('SILVER','first')  # Same SQS job UUID is already terminal.
    assert len(client.calls)==writes
    run('SILVER','first-reconcile')
    assert state_evidence(pipeline)[0]==first_head

    upload,file_id=new_delivery(pipeline,b'id,city,amount\n1,B,15\n3,C,30\n')
    run('BRONZE','next-bronze',upload=upload)
    if mode=='SNAPSHOT':
        declare_snapshot(ws,ds,user,upload,SnapshotDeliveryRequest(coverage='COMPLETE',effective_at='2026-10-02T00:00:00Z'))
    # Pin a queued request, then genuinely advance its physical Bronze head.
    admit(source[1],upload,'SILVER','stale-input',isolated_test=True)
    reuse,_=new_delivery(pipeline,reuse_file=file_id)
    run('BRONZE','advance-bronze-head',upload=reuse)
    with pytest.raises(psycopg.DatabaseError,match='Admission input version changed'):
        run('SILVER','stale-input',upload=upload)
    assert state_evidence(pipeline)[0]==first_head

    active=admit(source[1],upload,'SILVER','interrupted-apply',isolated_test=True)
    competing=admit(source[1],upload,'SILVER','competing-apply',isolated_test=True)
    # Commit genuine Silver evidence before the bounded contention window.
    # A process crash leaves the job unfinished: it is never falsely ACKed.
    class ProcessCrash(BaseException): pass
    def stop_before_apply(*args): raise ProcessCrash('Interrupted after Silver publication')
    with monkeypatch.context() as patch:
        patch.setattr(run.adapter,'_apply',stop_before_apply)
        with pytest.raises(ProcessCrash):
            execution.execute_job(str(active),boundary_executor=run.adapter,remaining_time_ms=lambda:3600000)
    with source[0]() as conn:
        assert conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s',(active,)).fetchone()[0]=='RUNNING'
        conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(active,))
    entered,release=Event(),Event()
    publish=ApplyGuard.publish_incremental
    def interrupted(guard,conn,application_id):
        # Silver receipt is committed, candidate validated, dataset lock held.
        entered.set()
        if not release.wait(120): raise TimeoutError('Acceptance test did not release publication')
        raise RuntimeError('Injected application publication interruption')
    with monkeypatch.context() as patch:
        patch.setattr(ApplyGuard,'publish_incremental',interrupted)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(execution.execute_job,str(active),boundary_executor=run.adapter,remaining_time_ms=lambda:3600000)
            try:
                assert entered.wait(180)
                with pytest.raises(RuntimeError,match='Dataset processing is already active'):
                    execution.execute_job(str(competing),boundary_executor=run.adapter,remaining_time_ms=lambda:3600000)
                assert state_evidence(pipeline)[0]==first_head
            finally: release.set()
            with pytest.raises(RuntimeError,match='previous trusted state'): future.result(timeout=120)
    with source[0]() as conn:
        assert conn.execute('SELECT status,failure_code FROM delivery_applications WHERE upload_request_id=%s',(upload,)).fetchone()==('FAILED','APPLICATION_FAILED')
        assert conn.execute('SELECT count(*) FROM dataset_state_versions WHERE status=\'PUBLISHED\'').fetchone()[0]==1
        silver_attempts=conn.execute("SELECT count(*) FROM processing_attempts WHERE stage='SILVER'").fetchone()[0]
        conn.execute("UPDATE processing_jobs SET available_at=clock_timestamp()-interval '1 second' WHERE job_id IN (%s,%s)",(active,competing))
    assert state_evidence(pipeline)[:2]==(first_head,first_manifest)
    run('SILVER','interrupted-apply',upload=upload)
    head,manifest,frame=state_evidence(pipeline)
    assert head!=first_head and manifest!=first_manifest
    assert dict(zip(frame['id'],frame['amount']))=={1:15,2:20,3:30}
    assert frame.loc[frame.id==1,'_datarise_origin_upload_id'].iloc[0]==source[2]
    assert frame.loc[frame.id==1,'_datarise_upload_id'].iloc[0]==upload
    if mode=='SNAPSHOT':
        assert dict(zip(frame['id'],frame['_datarise_active']))=={1:True,2:False,3:True}
        assert frame.loc[frame.id==2,'_datarise_deactivated_upload_id'].iloc[0]==upload
    run('SILVER','competing-apply',upload=upload)
    with source[0]() as conn:
        assert conn.execute("SELECT count(*) FROM processing_attempts WHERE stage='SILVER'").fetchone()[0]==silver_attempts
        assert conn.execute("SELECT count(*) FROM delivery_applications WHERE status='SUCCESS'").fetchone()[0]==2
        assert conn.execute('SELECT inserted_rows,updated_rows FROM delivery_applications WHERE upload_request_id=%s',(upload,)).fetchone()==(1,1)
        assert conn.execute('SELECT attempt_count,status FROM processing_jobs WHERE job_id=%s',(active,)).fetchone()==(3,'SUCCEEDED')
    assert state_evidence(pipeline)[0]==head
    run('GOLD','keyed-gold',upload=upload)
