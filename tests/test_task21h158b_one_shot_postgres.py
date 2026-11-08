"""One-shot real stages, isolated PG authority and versioned in-memory objects."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
import psycopg
from app.db import processing_job_repository as jobs
from app.processing_worker.dispatch import admit
from app.processing_worker.one_shot import run_once, Outcome, Stage
from tests.test_rule_approval_postgres import pg
from tests.test_processing_jobs_postgres import source
from tests.test_task20_processing_postgres import pipeline, approve


@pytest.fixture
def once(pipeline):
    source,storage,old_run=pipeline
    def approval():
        from app.processing_worker.runtime_security import require_runtime
        with source[0]() as conn:require_runtime(conn,'worker',isolated_test=True)
        return True
    def run(job,**kw):
        return run_once(job,executor=old_run.adapter,runtime_approval=approval,budget_seconds=300,**kw)
    def create(stage,key=None):return admit(source[1],source[2],stage,key or stage,isolated_test=True)
    return source,storage,old_run.adapter,create,run


def test_real_three_stages_rule_pause_duplicate_and_receipt_reconciliation(once):
    src,storage,adapter,create,run=once
    bronze=create('BRONZE');result=run(bronze)
    assert result.outcome==Outcome.SUCCEEDED and result.stage==Stage.BRONZE and result.exit_code==0
    with pytest.raises(psycopg.DatabaseError,match='Approved'):
        create('SILVER')
    association=approve(src)
    for stage in ('SILVER','GOLD'):
        job=create(stage);result=run(job)
        assert result.outcome==Outcome.SUCCEEDED and result.stage==Stage(stage)
        writes=len(storage.calls)
        assert run(job).outcome==Outcome.ALREADY_SUCCEEDED
        assert len(storage.calls)==writes
    # Different intentional Gold job can reconcile the same trusted stage result.
    writes=sum(c[0]=='put' for c in storage.calls)
    assert run(create('GOLD','reconcile')).outcome==Outcome.SUCCEEDED
    assert sum(c[0]=='put' for c in storage.calls)==writes
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==3
        assert conn.execute("SELECT count(*) FROM processing_attempts WHERE status='SUCCESS'").fetchone()[0]==3
        assert conn.execute("SELECT count(*) FROM processing_jobs WHERE status='SUCCEEDED'").fetchone()[0]==4
        assert conn.execute('SELECT total_rows,valid_rows,rejected_rows FROM data_quality_runs').fetchone()==(2,2,0)
        assert conn.execute('SELECT status FROM dataset_version_files WHERE dataset_version_file_id=%s',(association,)).fetchone()[0]=='SUCCESS'


@pytest.mark.parametrize('loss',['before_completion','after_commit'])
def test_completion_loss_recovers_without_duplicate_publication(once,monkeypatch,loss):
    src,storage,adapter,create,run=once;job=create('BRONZE');finish=jobs.finish_job
    def lost(*args,**kwargs):
        if loss=='after_commit':finish(*args,**kwargs)
        raise ConnectionError('SYNTHETIC_PRIVATE')
    with monkeypatch.context() as patch:
        patch.setattr(jobs,'finish_job',lost)
        result=run(job)
    assert result.outcome==Outcome.RETRY_REQUIRED and not result.completion_observed
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1
        if loss=='before_completion':conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job,))
    writes=sum(c[0]=='put' for c in storage.calls)
    result=run(job)
    assert result.outcome==(Outcome.SUCCEEDED if loss=='before_completion' else Outcome.ALREADY_SUCCEEDED)
    assert sum(c[0]=='put' for c in storage.calls)==writes
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM processing_attempts').fetchone()[0]==1
        assert conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()[0]=='SUCCEEDED'


def test_simultaneous_invocations_overlap_with_one_claim_and_publication(once):
    src,storage,adapter,create,run=once;job=create('BRONZE');entered,release=Event(),Event();verify=adapter.verifier
    def held(candidate):
        entered.set()
        if not release.wait(120):raise TimeoutError('Test did not release verifier')
        return verify(candidate)
    adapter.verifier=held
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            active=pool.submit(run,job)
            try:
                assert entered.wait(120)
                with src[0]() as conn:
                    assert conn.execute("SELECT status,lease_expires_at>clock_timestamp() FROM processing_jobs WHERE job_id=%s",(job,)).fetchone()==('RUNNING',True)
                    assert conn.execute('SELECT count(*) FROM processing_attempts').fetchone()[0]==1
                competing=run(job)
                assert competing.outcome==Outcome.BUSY and competing.exit_code==75
            finally:release.set()
            assert active.result(timeout=120).outcome==Outcome.SUCCEEDED
    finally:adapter.verifier=verify;release.set()
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1
        assert conn.execute('SELECT attempt_count FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()[0]==1


@pytest.mark.parametrize('failure',['verification','expired','stolen','cancel'])
def test_publication_denied_on_verifier_or_execution_ownership_loss(once,failure):
    src,storage,adapter,create,run=once;job=create('BRONZE');verify=adapter.verifier;stop=Event();replacement=[]
    def fail(candidate):
        if failure=='verification':raise ValueError('SYNTHETIC_PRIVATE verifier rejected')
        if failure=='cancel':stop.set()
        else:
            with src[0]() as conn:conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job,))
            if failure=='stolen':
                claimed=jobs.claim_job_result(job)
                assert claimed.outcome==jobs.ClaimOutcome.CLAIMED
                replacement.append(claimed.lease)
        return verify(candidate)
    adapter.verifier=fail
    try:result=run(job,stop=stop)
    finally:adapter.verifier=verify
    assert result.exit_code==75 and not result.completion_observed
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==0
        assert conn.execute("SELECT count(*) FROM processing_artifact_candidates WHERE status='VERIFIED'").fetchone()[0]==(1 if failure=='cancel' else 0)
        if replacement:
            assert conn.execute('SELECT lease_token,attempt_count,status FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()==(replacement[0].lease_token,2,'RUNNING')
    # Legitimate retry/reclaim after failure must recover, not publish old work.
    with src[0]() as conn:
        conn.execute("UPDATE processing_jobs SET available_at=clock_timestamp()-interval '1 second',lease_expires_at=CASE WHEN status='RUNNING' THEN clock_timestamp()-interval '1 second' ELSE NULL END WHERE job_id=%s",(job,))
    assert run(job).outcome==Outcome.SUCCEEDED
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1


@pytest.mark.parametrize('drift',['ownership','rules','source_head'])
def test_authoritative_drift_prevents_one_shot_processing(once,drift):
    src,storage,adapter,create,run=once
    if drift=='ownership':job=create('BRONZE')
    else:
        assert run(create('BRONZE')).outcome==Outcome.SUCCEEDED
        association=approve(src);job=create('SILVER','pinned-silver')
    if drift=='source_head':
        # A real new verified Bronze head invalidates already-admitted Silver.
        from tests.test_task20_processing_postgres import new_delivery
        with src[0]() as conn:file_id=conn.execute('SELECT file_id FROM upload_requests WHERE upload_id=%s',(src[2],)).fetchone()[0]
        upload,_=new_delivery((src,storage,None),reuse_file=file_id)
        next_bronze=admit(src[1],upload,'BRONZE','new-bronze-head',isolated_test=True)
        assert run(next_bronze).outcome==Outcome.SUCCEEDED
    else:
        with src[0]() as conn:
            if drift=='ownership':conn.execute("UPDATE datasets SET owner='dev:other'")
            else:conn.execute('UPDATE dataset_version_files SET applied_rule_version=NULL WHERE dataset_version_file_id=%s',(association,))
    writes=sum(c[0]=='put' for c in storage.calls)
    result=run(job)
    assert result.outcome==(Outcome.SOURCE_INELIGIBLE if drift=='ownership' else Outcome.RETRY_REQUIRED)
    assert sum(c[0]=='put' for c in storage.calls)==writes
    with src[0]() as conn:
        assert conn.execute("SELECT count(*) FROM processing_attempts WHERE stage='SILVER'").fetchone()[0]==0
