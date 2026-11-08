"""Independent real processes/connections and existing trusted pipeline in UUID schemas."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import time
import pytest
import psycopg

from app.db import processing_job_repository as jobs
from app.processing_worker.dispatch import admit
from app.processing_worker.supervision import supervise,Settings
from app.processing_worker.one_shot import Outcome
from tests.test_rule_approval_postgres import pg
from tests.test_processing_jobs_postgres import source
from tests.test_task20_processing_postgres import pipeline,approve
from task21h158d_support import pg_agent,pg_child,FileClient
from tests.test_task21h158d_supervision import until


@pytest.fixture
def runtime(pipeline,tmp_path):
    src,memory,_=pipeline
    with src[0]() as conn:schema=conn.execute('SELECT current_schema()').fetchone()[0]
    client=FileClient(tmp_path/'objects')
    for (key,version),payload in memory.objects.items():client.seed(key,payload,version)
    scratch=tmp_path/'scratch';scratch.mkdir()
    def create(stage,key=None):return admit(src[1],src[2],stage,key or stage,isolated_test=True)
    def run(job,*,fault=None,settings=None,stop=None):
        cfg=dict(root=str(tmp_path),schema=schema,actor=src[1],fault=fault)
        return supervise(job,agent_factory=pg_agent,child_factory=pg_child,configuration=cfg,approval=lambda:True,
                         settings=settings or Settings(budget_seconds=90,lease_seconds=120,heartbeat_seconds=1,database_response_seconds=5,grace_seconds=.2,kill_seconds=.5),stop=stop,scratch_directory=scratch)
    return src,client,tmp_path,create,run


def released(src):
    from app.services.dataset_processing_service import dataset_lock
    with src[0]() as conn:
        schema,ws,ds,file=conn.execute('SELECT current_schema(),workspace_id,dataset_id,file_id FROM upload_requests WHERE upload_id=%s',(src[2],)).fetchone()
    with dataset_lock(ws,ds):pass
    with src[0]() as conn:
        key=f'{schema}:task20-bronze:{file}'
        assert conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',(key,)).fetchone()[0]
        conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))


def test_real_bronze_silver_gold_and_duplicates(runtime):
    src,client,root,create,run=runtime
    job=create('BRONZE');result=run(job)
    with src[0]() as conn:status=conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()[0]
    assert result.result.outcome==Outcome.SUCCEEDED and result.children_reaped,(result.as_dict(),status,(root/'agent-error').read_text() if (root/'agent-error').exists() else None)
    with pytest.raises(psycopg.DatabaseError,match='Approved'):create('SILVER')
    approve(src)
    for stage in ('SILVER','GOLD'):
        job=create(stage);result=run(job)
        assert result.result.outcome==Outcome.SUCCEEDED and result.children_reaped
        count=len(client._latest())
        assert run(job).result.outcome==Outcome.ALREADY_SUCCEEDED
        assert len(client._latest())==count
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==3
        assert conn.execute("SELECT count(*) FROM processing_jobs WHERE status='SUCCEEDED'").fetchone()[0]==3
    released(src)


def test_real_120_second_lease_30_second_independent_heartbeat_during_block(runtime):
    src,client,root,create,run=runtime;job=create('BRONZE');settings=Settings(budget_seconds=60,grace_seconds=.2,kill_seconds=.5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        active=pool.submit(run,job,fault='wait_release',settings=settings)
        try:
            until(lambda:(root/'ready').exists(),15)
            with src[0]() as conn:
                first=conn.execute('SELECT lease_expires_at FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()[0]
            from app.services.dataset_processing_service import dataset_lock
            with src[0]() as conn:ws,ds=conn.execute('SELECT workspace_id,dataset_id FROM upload_requests WHERE upload_id=%s',(src[2],)).fetchone()
            with pytest.raises(RuntimeError,match='already active'):
                with dataset_lock(ws,ds):pass
            assert run(job).result.outcome==Outcome.BUSY
            until(lambda:(root/'renewals').exists() and len((root/'renewals').read_text().splitlines())>=2,35)
            with src[0]() as conn:
                expiry,token=conn.execute('SELECT lease_expires_at,lease_token FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()
                assert expiry>first and token is not None
        finally:(root/'release').touch()
        result=active.result(timeout=15)
    assert result.result.outcome==Outcome.SUCCEEDED and result.renewals>=2 and result.children_reaped
    released(src)


@pytest.mark.parametrize('fault',['heartbeat_fail','expired','cancel','block'])
def test_blocked_verified_candidate_cannot_publish_after_stop(runtime,fault):
    src,client,root,create,run=runtime;job=create('BRONZE')
    result=run(job,fault=fault,settings=Settings(budget_seconds=12,lease_seconds=10,heartbeat_seconds=.5,database_response_seconds=3,grace_seconds=.2,kill_seconds=.5))
    assert (root/'ready').exists() and result.exit_code!=0 and result.children_reaped
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM processing_stage_heads').fetchone()[0]==0
        assert conn.execute("SELECT count(*) FROM processing_artifact_candidates WHERE status='VERIFIED'").fetchone()[0]==1
        status=conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()[0]
        if fault=='cancel':assert status=='CANCELLED'
    released(src)
    before=(root/'renewals').read_bytes();Event().wait(.1);assert (root/'renewals').read_bytes()==before


@pytest.mark.parametrize('fault',['crash_write','completion_fail'])
def test_interruption_and_completion_loss_recover_without_second_publication(runtime,fault):
    src,client,root,create,run=runtime;job=create('BRONZE')
    result=run(job,fault=fault)
    assert result.exit_code!=0 and result.children_reaped
    count=len(client._latest())
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==(1 if fault=='completion_fail' else 0)
        conn.execute("UPDATE processing_jobs SET available_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job,))
    released(src)
    assert run(job).result.outcome==Outcome.SUCCEEDED
    with src[0]() as conn:assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1
    if fault=='completion_fail':assert len(client._latest())==count


def test_approval_change_fails_before_writes(runtime):
    src,client,root,create,run=runtime
    assert run(create('BRONZE')).result.outcome==Outcome.SUCCEEDED
    association=approve(src);job=create('SILVER')
    with src[0]() as conn:conn.execute('UPDATE dataset_version_files SET applied_rule_version=NULL WHERE dataset_version_file_id=%s',(association,))
    count=len(client._latest());result=run(job)
    assert result.exit_code!=0 and result.children_reaped
    assert len(client._latest())==count
    with src[0]() as conn:assert conn.execute("SELECT count(*) FROM processing_stage_receipts WHERE stage='SILVER'").fetchone()[0]==0


def test_parent_hard_death_releases_real_locks_and_stale_candidate_cannot_publish(runtime):
    import json,os,subprocess,sys
    src,client,root,create,run=runtime;job=create('BRONZE')
    with src[0]() as conn:schema=conn.execute('SELECT current_schema()').fetchone()[0]
    env=dict(os.environ,PYTHONPATH=os.pathsep.join((str(Path('tests').resolve()),str(Path.cwd()))))
    process=subprocess.Popen([sys.executable,'-B','-c','from task21h158d_support import pg_host; pg_host()'],
        stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,text=True)
    request=dict(job=str(job),configuration=dict(root=str(root),schema=schema,actor=src[1],fault='block'),scratch=str(root/'scratch'))
    process.stdin.write(json.dumps(request));process.stdin.close()
    try:
        until(lambda:(root/'ready').exists(),20)
        with src[0]() as conn:
            token=conn.execute('SELECT lease_token FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()[0]
            candidate=conn.execute('SELECT candidate_id FROM processing_artifact_candidates').fetchone()[0]
        process.kill();process.wait(timeout=5)
        def gone():
            try:released(src);return True
            except RuntimeError:return False
        until(gone,5)
        before=(root/'renewals').read_bytes();Event().wait(.1);assert (root/'renewals').read_bytes()==before
        with src[0]() as conn:
            assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==0
            conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job,))
        assert run(job).result.outcome==Outcome.SUCCEEDED
        assert jobs.finish_job(job,token) is None
        with src[0]() as conn:
            with pytest.raises(psycopg.DatabaseError,match='lease'):
                conn.execute('SELECT task20_publish(%s,%s,%s)',(job,token,candidate))
        with src[0]() as conn:assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)


def test_real_retained_cap_preserves_reconcile_and_rejects_another_delivery(runtime):
    from tests.test_task20_processing_postgres import new_delivery
    from app.schemas.incremental import LoadPolicyRequest
    from app.schemas.business_rules import BusinessRuleAnswer
    from app.services import processing_context_service as context,business_rule_service as rules
    from app.services.incremental_policy_service import save_policy
    src,client,root,create,run=runtime
    payload=b'id,city,amount\n'+b''.join(f'{i},A,10\n'.encode() for i in range(1000))
    upload,file=new_delivery((src,client,None),payload)
    def stage(name,key):return admit(src[1],upload,name,key,isolated_test=True)
    assert run(stage('BRONZE','large-bronze')).result.outcome==Outcome.SUCCEEDED
    with src[0]() as conn:
        association,ws,ds,version=conn.execute('''SELECT f.dataset_version_file_id,d.workspace_id,d.dataset_id,v.dataset_version_id
            FROM dataset_version_files f JOIN dataset_versions v USING(dataset_version_id) JOIN datasets d USING(dataset_id)
            WHERE f.file_id=%s''',(file,)).fetchone()
    user=dict(user_id=src[1],owner_key='dev:test')
    with context.rule_mutation(association,user,0,ws,ds):
        rules.submit_business_rule_answers(association,[BusinessRuleAnswer(column_name='id',rule_type='NOT_NULL',answer='YES')])
    with context.rule_mutation(association,user,1,ws,ds):assert rules.finalize_business_rules(association)['finalized']
    save_policy(ws,ds,user,LoadPolicyRequest(dataset_version_id=version,expected_policy_version=0,load_strategy='APPEND',business_keys=[],schema_evolution_policy='STRICT'))
    assert run(stage('SILVER','large-silver')).result.outcome==Outcome.SUCCEEDED
    with src[0]() as conn:assert conn.execute('SELECT s.row_count FROM datasets d JOIN dataset_state_versions s ON s.state_id=d.current_state_id WHERE d.dataset_id=%s',(ds,)).fetchone()[0]==1000
    count=len(client._latest())
    assert run(stage('SILVER','reconcile-at-cap')).result.outcome==Outcome.SUCCEEDED
    assert len(client._latest())==count
    next_upload,_=new_delivery((src,client,None),reuse_file=file)
    job=admit(src[1],next_upload,'SILVER','overflow',isolated_test=True)
    assert run(job).exit_code!=0
    assert len(client._latest())==count
    with src[0]() as conn:assert conn.execute('SELECT s.row_count FROM datasets d JOIN dataset_state_versions s ON s.state_id=d.current_state_id WHERE d.dataset_id=%s',(ds,)).fetchone()[0]==1000


def test_rule_revocation_during_blocking_stage_stops_independent_renewal(runtime):
    src,client,root,create,run=runtime
    assert run(create('BRONZE')).result.outcome==Outcome.SUCCEEDED
    association=approve(src);job=create('SILVER')
    with ThreadPoolExecutor(max_workers=1) as pool:
        active=pool.submit(run,job,fault='block')
        until(lambda:(root/'ready').exists(),15)
        with src[0]() as conn:conn.execute('UPDATE dataset_version_files SET applied_rule_version=NULL WHERE dataset_version_file_id=%s',(association,))
        result=active.result(timeout=12)
    assert result.exit_code!=0 and result.children_reaped
    with src[0]() as conn:assert conn.execute("SELECT count(*) FROM processing_stage_receipts WHERE stage='SILVER'").fetchone()[0]==0
    released(src)
