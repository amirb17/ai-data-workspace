"""Real disposable PostgreSQL + FakeBatch. No cloud, SDK, roles or execution bootstrap."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
from dataclasses import replace
from threading import Event
from uuid import uuid4
import pytest
import psycopg

from app.db import database, processing_job_repository as jobs
from app.db.batch_scheduling_repository import BatchRepository
from app.processing_worker.batch_scheduling import dispatch_once, reconcile_once, DispatchOutcome, DefinitelyNotSubmitted
from tests.test_rule_approval_postgres import pg
from tests.test_processing_jobs_postgres import source
from tests.test_task20_processing_postgres import pipeline
from tests.test_task21h158b_one_shot_postgres import once
from tests.test_task21h158c_batch import runtime, FakeBatch


@pytest.fixture
def batch(pipeline):
    src,storage,run=pipeline
    repo=BatchRepository();fake=FakeBatch()
    job=repo.admit(src[1],src[2],'batch',runtime())
    return src,repo,fake,job


def dispatch(repo,fake):return dispatch_once(repo,fake,approval=lambda:True)
def reconcile(repo,fake,sid):return reconcile_once(repo,fake,sid,approval=lambda:True)


def test_sql_routing_before_claim_and_immutable_admission(batch):
    src,repo,fake,job=batch
    with src[0]() as conn:
        assert conn.execute('SELECT * FROM task20_claim_dispatch()').fetchone() is None
    assert repo.admit(src[1],src[2],'batch',runtime())==job
    with pytest.raises(psycopg.DatabaseError,match='route conflict'):
        repo.admit(src[1],src[2],'batch',replace(runtime(),queue='other'))
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED
    assert dispatch(repo,fake)==DispatchOutcome.IDLE
    assert fake.calls[0].job_id==job


def test_existing_sqs_route_and_already_claimed_cannot_be_rerouted(pipeline):
    src,_,_=pipeline;repo=BatchRepository()
    from app.processing_worker.dispatch import admit
    job=admit(src[1],src[2],'BRONZE','sqs',isolated_test=True)
    with src[0]() as conn:
        claimed=conn.execute('SELECT * FROM task20_claim_dispatch()').fetchone()
        assert claimed[0]==job
    with pytest.raises(psycopg.DatabaseError,match='fresh'):
        repo.admit(src[1],src[2],'sqs',runtime())
    assert repo.claim() is None


def test_actual_overlapping_dispatchers_committed_before_provider(batch):
    src,repo,fake,job=batch;entered,release=Event(),Event();submit=fake.submit
    def held(intent):
        with src[0]() as conn:
            assert conn.execute('SELECT state,claim_until>clock_timestamp() FROM processing_batch_submissions WHERE submission_id=%s',
                                (intent.submission_id,)).fetchone()==('CLAIMED',True)
        entered.set()
        if not release.wait(90):raise TimeoutError('Test coordination expired')
        return submit(intent)
    fake.submit=held
    with ThreadPoolExecutor(max_workers=1) as pool:
        active=pool.submit(dispatch,repo,fake)
        try:
            assert entered.wait(90)
            assert dispatch(repo,fake)==DispatchOutcome.IDLE
            with src[0]() as conn:assert conn.execute('SELECT * FROM task20_claim_dispatch()').fetchone() is None
        finally:release.set()
        assert active.result(timeout=90)==DispatchOutcome.SUBMITTED
    assert len(fake.calls)==1


@pytest.mark.parametrize('crash',['before_submit','after_submit','ambiguous'])
def test_uncertainty_never_resubmits_even_without_notifications(batch,crash):
    src,repo,fake,job=batch
    if crash=='before_submit':intent=repo.claim()
    else:
        submit=fake.submit
        def uncertain(i):
            submit(i)
            if crash=='after_submit':raise KeyboardInterrupt()
            raise TimeoutError('PRIVATE')
        fake.submit=uncertain
        if crash=='after_submit':
            with pytest.raises(KeyboardInterrupt):dispatch(repo,fake)
        else:assert dispatch(repo,fake)==DispatchOutcome.UNCERTAIN
        intent=fake.calls[0]
    with src[0]() as conn:
        conn.execute("UPDATE processing_batch_submissions SET claim_until=clock_timestamp()-interval '1 second',reconcile_at=clock_timestamp()-interval '1 second'")
    assert repo.due()==[intent.submission_id]
    assert reconcile(repo,fake,intent.submission_id)=='UNCERTAIN'
    for _ in range(2):assert dispatch(repo,fake)==DispatchOutcome.IDLE
    with src[0]() as conn:
        assert conn.execute('SELECT submissions FROM processing_batch_routes').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM processing_batch_provider_jobs').fetchone()[0]==(0 if crash=='before_submit' else 1)


@pytest.mark.parametrize('status',['SUCCEEDED','FAILED'])
def test_provider_terminal_cannot_complete_live_sql_lease(batch,status):
    src,repo,fake,job=batch
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED
    intent=fake.calls[0];lease=jobs.claim_job_result(job).lease
    fake.jobs=[replace(fake.jobs[0],status=status)]
    assert reconcile(repo,fake,intent.submission_id)=='LEASE_ACTIVE'
    with src[0]() as conn:
        assert conn.execute('SELECT status,lease_token FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()==('RUNNING',lease.lease_token)
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==0
        conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job,))
    assert reconcile(repo,fake,intent.submission_id)=='RETRY_READY'
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED
    assert len(fake.calls)==2


def test_duplicate_ids_history_and_active_duplicate_blocks_retry(batch):
    src,repo,fake,job=batch
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED
    intent=fake.calls[0];original=fake.jobs[0]
    fake.jobs=[replace(original,status='FAILED'),replace(original,provider_id=str(uuid4()))]
    assert reconcile(repo,fake,intent.submission_id)=='PROVIDER_ACTIVE'
    assert reconcile(repo,fake,intent.submission_id)=='PROVIDER_ACTIVE'
    assert dispatch(repo,fake)==DispatchOutcome.IDLE
    fake.jobs=[replace(o,status='FAILED') for o in fake.jobs]
    assert reconcile(repo,fake,intent.submission_id)=='RETRY_READY'
    with src[0]() as conn:assert conn.execute('SELECT count(*) FROM processing_batch_provider_jobs').fetchone()[0]==2


def test_definite_failure_budget_and_retry_due(batch):
    src,repo,fake,job=batch
    fake.behavior=lambda _:(_ for _ in ()).throw(DefinitelyNotSubmitted())
    for _ in range(3):assert dispatch(repo,fake)==DispatchOutcome.NOT_SUBMITTED
    assert dispatch(repo,fake)==DispatchOutcome.IDLE
    with src[0]() as conn:
        assert conn.execute('SELECT submissions FROM processing_batch_routes').fetchone()[0]==3
        assert conn.execute('SELECT attempt_count,status FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()==(0,'QUEUED')


def test_delayed_job_not_submitted(batch):
    src,repo,fake,job=batch
    with src[0]() as conn:conn.execute("UPDATE processing_jobs SET available_at=clock_timestamp()+interval '1 hour' WHERE job_id=%s",(job,))
    assert dispatch(repo,fake)==DispatchOutcome.IDLE
    assert not fake.calls


@pytest.mark.parametrize('change',['ownership','archive','disabled'])
def test_ineligible_authority_prevents_submission(batch,change):
    src,repo,fake,job=batch
    with src[0]() as conn:
        if change=='ownership':conn.execute("UPDATE datasets SET owner='dev:other'")
        elif change=='archive':conn.execute('UPDATE upload_requests SET archived_at=clock_timestamp(),archived_by=%s',(src[1],))
        else:conn.execute("UPDATE users SET account_status='DISABLED'")
    assert dispatch(repo,fake)==DispatchOutcome.IDLE
    assert not fake.calls
    with src[0]() as conn:assert conn.execute('SELECT status,failure_code FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()==('FAILED','SOURCE_INELIGIBLE')


def test_admission_and_claim_outer_rollback(batch):
    src,repo,fake,job=batch
    with pytest.raises(RuntimeError):
        with database.repository_transaction():
            assert repo.claim() is not None
            raise RuntimeError('rollback')
    with src[0]() as conn:
        assert conn.execute('SELECT submissions FROM processing_batch_routes').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM processing_batch_submissions').fetchone()[0]==0
    with pytest.raises(RuntimeError):
        with database.repository_transaction():
            repo.admit(src[1],src[2],'rolled-back',runtime())
            raise RuntimeError('rollback')
    with src[0]() as conn:assert conn.execute('SELECT count(*) FROM processing_jobs').fetchone()[0]==1
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED


def test_lost_response_persistence_claim_cas_and_lookup_failure(batch,monkeypatch):
    src,repo,fake,job=batch;response=repo.response
    with monkeypatch.context() as patch:
        patch.setattr(repo,'response',lambda *_:(_ for _ in ()).throw(ConnectionError('PRIVATE')))
        with pytest.raises(ConnectionError):dispatch(repo,fake)
    intent=fake.calls[0]
    assert not response(replace(intent,claim_token=uuid4()),str(uuid4()),'OBSERVED')
    with src[0]() as conn:conn.execute("UPDATE processing_batch_submissions SET claim_until=clock_timestamp()-interval '1 second'")
    with monkeypatch.context() as patch:
        patch.setattr(fake,'lookup',lambda _:(_ for _ in ()).throw(TimeoutError('PRIVATE')))
        with pytest.raises(TimeoutError):reconcile(repo,fake,intent.submission_id)
    assert reconcile(repo,fake,intent.submission_id)=='UNCERTAIN'
    assert dispatch(repo,fake)==DispatchOutcome.IDLE


@pytest.mark.parametrize('running',[False,True])
def test_owned_cancel_fences_claim_renew_finish_and_publication(batch,running):
    src,repo,fake,job=batch
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED
    lease=jobs.claim_job_result(job).lease if running else None
    with pytest.raises(psycopg.DatabaseError,match='ownership'):repo.cancel(job,src[1]+10000)
    assert repo.cancel(job,src[1])
    assert not repo.cancel(job,src[1])
    if lease:
        assert jobs.renew_job_lease(job,lease.lease_token) is None
        assert jobs.finish_job(job,lease.lease_token) is None
        with src[0]() as conn:
            with pytest.raises(psycopg.DatabaseError,match='lease'):conn.execute('SELECT task20_require_job(%s,%s)',(job,lease.lease_token))
    assert jobs.claim_job_result(job).outcome==jobs.ClaimOutcome.TERMINAL_FAILED
    assert reconcile(repo,fake,fake.calls[0].submission_id)=='TERMINAL'
    assert dispatch(repo,fake)==DispatchOutcome.IDLE


def test_fake_submission_real_one_shot_lost_completion_receipt_recovery(once,monkeypatch):
    src,storage,adapter,create,run=once;repo=BatchRepository();fake=FakeBatch()
    job=repo.admit(src[1],src[2],'batch-recovery',runtime())
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED
    intent=fake.calls[0];finish=jobs.finish_job
    with monkeypatch.context() as patch:
        patch.setattr(jobs,'finish_job',lambda *_a,**_k:(_ for _ in ()).throw(ConnectionError('PRIVATE')))
        assert run(intent.job_id,submission_id=intent.submission_id).outcome.value=='RETRY_REQUIRED'
    writes=sum(c[0]=='put' for c in storage.calls)
    assert reconcile(repo,fake,intent.submission_id)=='LEASE_ACTIVE'
    with src[0]() as conn:conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job,))
    fake.jobs=[replace(fake.jobs[0],status='FAILED')]
    assert reconcile(repo,fake,intent.submission_id)=='RETRY_READY'
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED
    assert run(job,submission_id=fake.calls[-1].submission_id).outcome.value=='SUCCEEDED'
    assert sum(c[0]=='put' for c in storage.calls)==writes
    assert reconcile(repo,fake,fake.calls[-1].submission_id)=='COMPLETED'
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM processing_attempts').fetchone()[0]==1


def test_cancellation_before_real_publication_denies_receipt(once):
    src,storage,adapter,create,run=once;repo=BatchRepository();fake=FakeBatch()
    job=repo.admit(src[1],src[2],'batch-cancel-publication',runtime())
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED
    verify=adapter.verifier
    def cancelling(candidate):
        result=verify(candidate)
        assert repo.cancel(job,src[1])
        return result
    adapter.verifier=cancelling
    try:result=run(job)
    finally:adapter.verifier=verify
    assert not result.completion_observed and result.exit_code==75
    with src[0]() as conn:
        assert conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()[0]=='CANCELLED'
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0]==0
        assert conn.execute("SELECT count(*) FROM processing_artifact_candidates WHERE status='VERIFIED'").fetchone()[0]==1


def test_proposed_migration_privileges_and_atomic_reapplication(batch):
    from pathlib import Path
    src,repo,fake,job=batch
    with src[0]() as conn:
        schema=conn.execute('SELECT current_schema()').fetchone()[0]
        functions=conn.execute("""SELECT p.proconfig,EXISTS(
            SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
            WHERE a.grantee=0 AND a.privilege_type='EXECUTE') FROM pg_proc p
            WHERE pronamespace=current_schema()::regnamespace AND proname LIKE 'task158c_%'""").fetchall()
        assert len(functions)==5
        for config,public in functions:
            assert not public and f'search_path=pg_catalog, {schema}, pg_temp' in config
        with pytest.raises(psycopg.errors.DuplicateTable):
            with conn.transaction():conn.execute(Path('migrations/0044_batch_scheduling.sql').read_text())
        assert conn.execute('SELECT count(*) FROM processing_batch_routes').fetchone()[0]==1


def test_reconciliation_rollback_correlation_and_provider_history_bound(batch):
    src,repo,fake,job=batch
    assert dispatch(repo,fake)==DispatchOutcome.SUBMITTED
    intent=fake.calls[0]
    # No forged event body can become an authoritative completion.
    fake.jobs=[replace(fake.jobs[0],fingerprint='b'*64)]
    with pytest.raises(ValueError,match='correlation'):reconcile(repo,fake,intent.submission_id)
    fake.jobs=[replace(fake.jobs[0],fingerprint=intent.fingerprint)]
    with pytest.raises(RuntimeError):
        with database.repository_transaction():
            assert repo.cancel(job,src[1])
            raise RuntimeError('rollback')
    assert jobs.claim_job_result(job).outcome==jobs.ClaimOutcome.CLAIMED
    observations=[replace(fake.jobs[0],provider_id=str(uuid4())) for _ in range(32)]
    with pytest.raises(psycopg.DatabaseError,match='history bound'):repo.reconcile(intent.submission_id,observations)
    with src[0]() as conn:assert conn.execute('SELECT count(*) FROM processing_batch_provider_jobs').fetchone()[0]==1


def test_exhausted_execution_budget_never_spends_submission(batch):
    src,repo,fake,job=batch
    with src[0]() as conn:
        conn.execute("""UPDATE processing_jobs SET status='RUNNING',attempt_count=max_attempts,
            lease_token=gen_random_uuid(),lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s""",(job,))
    assert dispatch(repo,fake)==DispatchOutcome.IDLE
    assert not fake.calls
    with src[0]() as conn:
        assert conn.execute('SELECT submissions FROM processing_batch_routes').fetchone()[0]==0
        assert conn.execute('SELECT status,failure_code FROM processing_jobs WHERE job_id=%s',(job,)).fetchone()==('FAILED','LEASE_EXPIRED')


def test_route_creation_and_sqs_claim_overlap_before_commit(pipeline):
    src,_,_=pipeline;repo=BatchRepository();ready,release=Event(),Event()
    def admitting():
        with database.repository_transaction():
            job=repo.admit(src[1],src[2],'atomic-route',runtime())
            ready.set()
            if not release.wait(90):raise TimeoutError('Route coordination expired')
        return job
    with ThreadPoolExecutor(max_workers=1) as pool:
        active=pool.submit(admitting)
        try:
            assert ready.wait(90)
            with src[0]() as conn:assert conn.execute('SELECT * FROM task20_claim_dispatch()').fetchone() is None
        finally:release.set()
        job=active.result(timeout=90)
    with src[0]() as conn:
        assert conn.execute('SELECT * FROM task20_claim_dispatch()').fetchone() is None
        assert conn.execute('SELECT attempts FROM processing_dispatch_outbox WHERE job_id=%s',(job,)).fetchone()[0]==0


def test_provider_observed_before_submit_response_is_idempotent(batch):
    src,repo,fake,job=batch;created,release=Event(),Event();submit=fake.submit
    def early_observation(intent):
        observation=submit(intent)
        created.set()
        if not release.wait(90):raise TimeoutError('Observation coordination expired')
        return observation
    fake.submit=early_observation
    with ThreadPoolExecutor(max_workers=1) as pool:
        active=pool.submit(dispatch,repo,fake)
        try:
            assert created.wait(90)
            assert reconcile(repo,fake,fake.calls[0].submission_id)=='SUBMITTING'
            with src[0]() as conn:assert conn.execute('SELECT count(*) FROM processing_batch_provider_jobs').fetchone()[0]==1
        finally:release.set()
        assert active.result(timeout=90)==DispatchOutcome.SUBMITTED
    with src[0]() as conn:
        assert conn.execute('SELECT state FROM processing_batch_submissions').fetchone()[0]=='OBSERVED'
        assert conn.execute('SELECT count(*) FROM processing_batch_provider_jobs').fetchone()[0]==1


def test_provider_identity_cannot_cross_concurrent_submissions(batch):
    from threading import Barrier
    src,repo,fake,job=batch
    repo.admit(src[1],src[2],'second-work',runtime())
    intents=[repo.claim(),repo.claim()]
    assert intents[0].job_id!=intents[1].job_id
    provider_id=str(uuid4());ready=Barrier(2)
    def response(intent):
        ready.wait(timeout=30)
        try:return repo.response(intent,provider_id,'OBSERVED')
        except psycopg.DatabaseError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(response,intents))
    assert sorted(results,key=str)==sorted([True,'rejected'],key=str)
    with src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_batch_provider_jobs').fetchone()[0]==1
        assert conn.execute("SELECT count(*) FROM processing_batch_submissions WHERE state='OBSERVED'").fetchone()[0]==1
        assert conn.execute("SELECT count(*) FROM processing_batch_submissions WHERE state='CLAIMED'").fetchone()[0]==1
