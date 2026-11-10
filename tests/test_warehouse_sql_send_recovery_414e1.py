"""414E1 actual PostgreSQL journal/CAS/fault tests; no provider IO or permissions."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict,replace
from pathlib import Path
from threading import Event
from uuid import uuid4
import os

from fastapi import HTTPException
import psycopg
import pytest

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_send_recovery_service as service
from tests.test_warehouse_sql_dispatch_checkpoint_414d5 import (
    pg,modeling_pg,fact_provider_pg,genuinely_served_query,served,check,request,scalar,
    state,change,blocked_by,new_linked_attempt,wait_for_database_deadline,
)

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def checkpointed(served):
    s=served
    assert os.getenv('RUN_DB_TESTS')=='1'
    with s.f.pg['connect']() as conn:
        conn.execute((ROOT/'migrations/0054_warehouse_query_send_recovery.sql').read_text())
    check(s)
    return s


def prepare(s,handle=None):
    return service.prepare_authenticated_send_intent(request=request(s.token),workspace_id=s.f.pg['workspace'],
        attempt_handle=handle or s.a.attempt_handle)


@pytest.fixture
def prepared(checkpointed):
    s=checkpointed;result=prepare(s)
    assert result.revision==1 and not result.execution_authorized
    return s


def view(s):return service.inspect_recovery(attempt_id=s.a.attempt_id)


def claim(s,seconds=30):
    return service.acquire_recovery(attempt_id=s.a.attempt_id,expected_revision=view(s).revision,lease_seconds=seconds)


def observe(lease,revision,value):
    return service.record_observation(lease=lease,expected_revision=revision,job=lease.job,observation=value)


def wait_lease(s,lease):
    with s.f.pg['connect']() as conn:
        conn.execute('SELECT pg_sleep(LEAST(2,GREATEST(0,EXTRACT(epoch FROM %s-clock_timestamp()))+0.01))',(lease.lease_until,))


def test_exact_intent_and_repeated_preparation_never_send_or_read_provider(prepared):
    s=prepared;calls=len(s.f.cloud.calls);result=prepare(s);v=view(s)
    assert result.revision==1 and result.classification=='NO_DISPATCH_REVIEW_REQUIRED'
    assert set(asdict(result))=={'attempt_handle','revision','classification'}
    assert not v.execution_authorized and not v.provider_dispatched
    assert v.job.project=='synthetic-project' and v.job.location=='asia-south1'
    assert v.job.job_id==f'dr_query_w{s.f.pg["workspace"]}_r{s.r}_{s.a.request_token[:32]}'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_send_intents')==1
    assert scalar(s,'SELECT count(*) FROM warehouse_query_send_journal')==1
    assert state(s)=='EXECUTING' and len(s.f.cloud.calls)==calls


def test_already_executing_legacy_claim_is_not_retroactively_given_send_intent(served):
    s=served
    with s.f.pg['connect']() as conn:conn.execute((ROOT/'migrations/0054_warehouse_query_send_recovery.sql').read_text())
    s.g.claim(s.r)
    with pytest.raises(repo.RecoveryConflict,match='CHECKPOINT_REQUIRED'):prepare(s)
    assert state(s)=='EXECUTING' and scalar(s,'SELECT count(*) FROM warehouse_query_send_intents')==0


@pytest.mark.parametrize('kind',['account','session','workspace','publication'])
def test_revocation_before_preparation_cannot_create_intent(checkpointed,kind):
    s=checkpointed;change(s,kind)
    with pytest.raises((HTTPException,repo.RecoveryConflict)):prepare(s)
    assert scalar(s,'SELECT count(*) FROM warehouse_query_send_intents')==0
    assert scalar(s,'SELECT status FROM query_governance_reservations WHERE reservation_id=%s',(s.r,))=='EXECUTING'


def test_creation_and_initial_journal_are_one_atomic_commit(checkpointed,monkeypatch):
    s=checkpointed;append=repo.append
    def crash(*args,**kwargs):
        append(*args,**kwargs)
        raise RuntimeError('Synthetic pre-commit intent failure')
    with monkeypatch.context() as patch:
        patch.setattr(repo,'append',crash)
        with pytest.raises(RuntimeError,match='intent failure'):prepare(s)
    assert scalar(s,'SELECT count(*) FROM warehouse_query_send_intents')==0
    assert scalar(s,'SELECT count(*) FROM warehouse_query_send_journal')==0
    assert state(s)=='EXECUTING'
    assert prepare(s).revision==1


def test_rollback_before_boundary_and_response_loss_after_boundary_are_distinct(prepared,monkeypatch):
    s=prepared;lease=claim(s);append=repo.append
    def crash(*args,**kwargs):
        append(*args,**kwargs)
        raise RuntimeError('Synthetic pre-commit boundary failure')
    with monkeypatch.context() as patch:
        patch.setattr(repo,'append',crash)
        with pytest.raises(RuntimeError,match='boundary failure'):
            service.record_send_boundary(lease=lease,expected_revision=lease.revision)
    assert view(s).phase=='INTENT_RECORDED' and view(s).revision==lease.revision
    result=service.record_send_boundary(lease=lease,expected_revision=lease.revision)
    assert result.phase=='SEND_UNKNOWN' and result.classification=='LOOKUP_REQUIRED'
    assert prepare(s).classification=='LOOKUP_REQUIRED'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_send_intents')==1
    # Simulate lost response: retrying old CAS cannot create another boundary.
    with pytest.raises(repo.RecoveryConflict,match='STALE_RECOVERY_REVISION'):
        service.record_send_boundary(lease=lease,expected_revision=lease.revision)
    with pytest.raises(repo.RecoveryConflict,match='ALREADY_RECORDED'):
        service.record_send_boundary(lease=lease,expected_revision=result.revision)
    assert scalar(s,"SELECT count(*) FROM warehouse_query_send_journal WHERE event='SEND_BOUNDARY'")==1


@pytest.mark.parametrize('observation',[service.Observation.NOT_FOUND,service.Observation.UNCERTAIN])
def test_missing_or_uncertain_job_after_possible_send_is_lookup_only(prepared,observation):
    s=prepared;lease=claim(s)
    sent=service.record_send_boundary(lease=lease,expected_revision=lease.revision)
    result=observe(lease,sent.revision,observation)
    assert result.phase=='SEND_UNKNOWN' and result.classification in ('NOT_FOUND_HELD','LOOKUP_RETRY')
    assert not result.execution_authorized and state(s)=='EXECUTING'
    assert scalar(s,'SELECT count(*) FROM query_governance_usage')==0


@pytest.mark.parametrize('observation',[service.Observation.SUCCEEDED,service.Observation.FAILED,service.Observation.CANCELLED])
def test_terminal_observation_is_unsettled_and_cannot_release_holds(prepared,observation):
    s=prepared;lease=claim(s)
    sent=service.record_send_boundary(lease=lease,expected_revision=lease.revision)
    running=observe(lease,sent.revision,service.Observation.IN_PROGRESS)
    result=observe(lease,running.revision,observation)
    assert result.phase==observation.value+'_OBSERVED' and result.classification=='TERMINAL_OBSERVED_HELD'
    assert state(s)=='EXECUTING' and scalar(s,'SELECT count(*) FROM query_governance_usage')==0
    assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))
    with pytest.raises(psycopg.DatabaseError,match='regress|contradict'):
        observe(lease,result.revision,service.Observation.IN_PROGRESS)
    assert view(s).phase==result.phase


def test_wrong_project_location_or_job_cannot_change_journal(prepared):
    s=prepared;lease=claim(s)
    for field,value in [('project','foreign-project'),('location','US'),('job_id','foreign-job')]:
        with pytest.raises(repo.RecoveryConflict,match='SCOPE_MISMATCH'):
            service.record_observation(lease=lease,expected_revision=lease.revision,
                job=replace(lease.job,**{field:value}),observation=service.Observation.NOT_FOUND)
    with pytest.raises(ValueError):observe(lease,lease.revision,'CANCEL_REQUESTED')
    assert view(s).revision==lease.revision


def test_expired_recovery_owner_is_fenced_and_reclaim_keeps_same_job_phase(prepared):
    s=prepared;first=claim(s,1)
    sent=service.record_send_boundary(lease=first,expected_revision=first.revision)
    wait_lease(s,first)
    with pytest.raises(repo.RecoveryConflict,match='EXPIRED_RECOVERY_OWNER'):
        observe(first,sent.revision,service.Observation.NOT_FOUND)
    second=claim(s)
    assert second.token!=first.token and second.generation==first.generation+1 and second.job==first.job
    assert view(s).phase=='SEND_UNKNOWN'
    with pytest.raises(repo.RecoveryConflict,match='RECOVERY_OWNER'):
        observe(first,second.revision,service.Observation.SUCCEEDED)
    assert observe(second,second.revision,service.Observation.NOT_FOUND).phase=='SEND_UNKNOWN'


@pytest.mark.parametrize('seconds',[0,121,True])
def test_recovery_lease_bounds_do_not_mutate_state(prepared,seconds):
    with pytest.raises(ValueError):claim(prepared,seconds)
    assert view(prepared).revision==1


@pytest.mark.parametrize('kind',['account','workspace','publication'])
def test_revocation_blocks_boundary_but_not_lookup_bookkeeping(prepared,kind):
    s=prepared;lease=claim(s);change(s,kind)
    assert view(s).classification=='REVOKED_HELD'
    with pytest.raises(repo.RecoveryConflict):service.record_send_boundary(lease=lease,expected_revision=lease.revision)
    result=observe(lease,lease.revision,service.Observation.NOT_FOUND)
    assert result.phase=='INTENT_RECORDED' and result.classification=='REVOKED_HELD'
    assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))


def test_revoke_after_boundary_never_clears_ambiguity_or_claim(prepared):
    s=prepared;lease=claim(s)
    sent=service.record_send_boundary(lease=lease,expected_revision=lease.revision)
    change(s,'account')
    result=observe(lease,sent.revision,service.Observation.UNCERTAIN)
    assert result.phase=='SEND_UNKNOWN' and result.revoked
    assert scalar(s,'SELECT status FROM query_governance_reservations WHERE reservation_id=%s',(s.r,))=='EXECUTING'


def test_checkpoint_lease_expiry_does_not_stop_lookup_or_allow_boundary(checkpointed):
    s=checkpointed;a,r=new_linked_attempt(s,lease_seconds=1);check(s,handle=a.attempt_handle)
    prepare(s,handle=a.attempt_handle)
    wait_for_database_deadline(s,r)
    lease=service.acquire_recovery(attempt_id=a.attempt_id,expected_revision=1)
    with pytest.raises(repo.RecoveryConflict,match='EXPIRED'):
        service.record_send_boundary(lease=lease,expected_revision=lease.revision)
    result=observe(lease,lease.revision,service.Observation.NOT_FOUND)
    assert result.classification=='EXPIRED_HELD' and result.phase=='INTENT_RECORDED'


def test_actual_duplicate_workers_serialize_one_lease_and_loser_has_stale_cas(prepared,monkeypatch):
    s=prepared;held,waiting,release=Event(),Event(),Event();pids={};append=repo.append;transaction=repo.transaction
    def pause(conn,*args,**kwargs):
        pids['holder']=conn.info.backend_pid;held.set()
        if not release.wait(30):raise TimeoutError('Test release missing')
        return append(conn,*args,**kwargs)
    from contextlib import contextmanager
    from app.db.database import repository_transaction
    @contextmanager
    def observed_transaction(workspace_id=None):
        # Instrument the second real backend before it waits on global scope.
        from threading import current_thread
        if current_thread().name.startswith('second'):
            with repository_transaction() as conn:
                conn.execute("SET LOCAL lock_timeout='5s'");conn.execute("SET LOCAL statement_timeout='10s'")
                pids['waiter']=conn.info.backend_pid;waiting.set()
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('datarise-query-governance-global',))
                yield conn
        else:
            with transaction(workspace_id) as conn:yield conn
    with monkeypatch.context() as patch:
        patch.setattr(repo,'append',pause);patch.setattr(repo,'transaction',observed_transaction)
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='first') as first, ThreadPoolExecutor(max_workers=1,thread_name_prefix='second') as second:
            a=first.submit(service.acquire_recovery,attempt_id=s.a.attempt_id,expected_revision=1)
            try:
                assert held.wait(10)
                b=second.submit(service.acquire_recovery,attempt_id=s.a.attempt_id,expected_revision=1)
                assert waiting.wait(10);blocked_by(s,pids['waiter'],pids['holder'])
            finally:release.set()
            assert a.result(timeout=30).revision==2
            with pytest.raises(repo.RecoveryConflict,match='STALE_RECOVERY_REVISION'):b.result(timeout=30)
    assert scalar(s,"SELECT count(*) FROM warehouse_query_send_journal WHERE event='LEASE_ACQUIRED'")==1


def test_journal_and_intent_reject_mutation_and_phase_reset(prepared):
    s=prepared;lease=claim(s);sent=service.record_send_boundary(lease=lease,expected_revision=lease.revision)
    with s.f.pg['connect']() as conn:
        for sql in ('DELETE FROM warehouse_query_send_intents',"UPDATE warehouse_query_send_journal SET phase='INTENT_RECORDED'",
                    'DELETE FROM warehouse_query_send_journal','TRUNCATE warehouse_query_send_journal','TRUNCATE warehouse_query_send_intents'):
            with pytest.raises(psycopg.DatabaseError):
                with conn.transaction():conn.execute(sql)
        with pytest.raises(psycopg.DatabaseError,match='Initial|Invalid|Stale'):
            with conn.transaction():
                conn.execute("INSERT INTO warehouse_query_send_journal SELECT attempt_id,revision+1,'CREATED','INTENT_RECORDED',NULL,lease_token,lease_generation,lease_until,clock_timestamp() FROM warehouse_query_send_journal WHERE attempt_id=%s AND revision=%s",(s.a.attempt_id,sent.revision))
        with pytest.raises(psycopg.errors.DuplicateTable):
            with conn.transaction():conn.execute((ROOT/'migrations/0054_warehouse_query_send_recovery.sql').read_text())
    assert view(s).phase=='SEND_UNKNOWN'


def test_old_settlement_remains_authoritative_and_new_metadata_never_releases_hold(prepared):
    s=prepared;lease=claim(s);observe(lease,lease.revision,service.Observation.FAILED)
    s.g.fail(s.r,bytes_processed=0,bytes_billed=0,provider_job_id=None,duration_ms=1)
    assert view(s).classification=='SETTLED_HELD'
    with pytest.raises(repo.RecoveryConflict,match='SETTLED_HELD'):claim(s)
    assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))



def test_wrong_token_and_stale_revision_are_fenced_without_mutation(prepared):
    s=prepared;lease=claim(s)
    with pytest.raises(repo.RecoveryConflict,match='RECOVERY_OWNER'):
        observe(replace(lease,token=uuid4()),lease.revision,service.Observation.NOT_FOUND)
    with pytest.raises(repo.RecoveryConflict,match='STALE_RECOVERY_REVISION'):
        observe(lease,lease.revision-1,service.Observation.NOT_FOUND)
    assert view(s).revision==lease.revision


def test_initial_event_is_a_commit_time_database_invariant(prepared):
    s=prepared;a,r=new_linked_attempt(s);check(s,handle=a.attempt_handle)
    with pytest.raises(psycopg.DatabaseError,match='atomic initial journal'):
        with s.f.pg['connect']() as conn:
            conn.execute("INSERT INTO warehouse_query_send_intents(attempt_id,reservation_id,provider_project,provider_location,provider_job_id,compiled_intent_sha256) SELECT c.attempt_id,c.reservation_id,i.provider_project,i.provider_location,c.provider_job_id,c.compiled_intent_sha256 FROM warehouse_query_dispatch_checkpoints c CROSS JOIN warehouse_query_send_intents i WHERE c.attempt_id=%s AND i.attempt_id=%s",(a.attempt_id,s.a.attempt_id))
    assert scalar(s,'SELECT count(*) FROM warehouse_query_send_intents WHERE attempt_id=%s',(a.attempt_id,))==0
    assert prepare(s,handle=a.attempt_handle).revision==1



@pytest.mark.parametrize('revocation_first',[False,True])
def test_real_revocation_and_boundary_transactions_serialize_without_dispatch(prepared,monkeypatch,revocation_first):
    s=prepared;lease=claim(s);held,waiting,release=Event(),Event(),Event();pids={}
    append=repo.append;live=service._live_context
    def revoke():
        with s.f.pg['connect']() as conn:
            if not revocation_first:
                pids['waiter']=conn.info.backend_pid;waiting.set()
            conn.execute("UPDATE users SET account_status='DISABLED' WHERE user_id=%s",(s.f.pg['owner']['user_id'],))
            if revocation_first:
                pids['holder']=conn.info.backend_pid;held.set()
                if not release.wait(30):raise TimeoutError('Revocation release missing')
    def pause_append(conn,*args,**kwargs):
        pids['holder']=conn.info.backend_pid;held.set()
        if not release.wait(30):raise TimeoutError('Boundary release missing')
        return append(conn,*args,**kwargs)
    def observe_live(conn,*args):
        pids['waiter']=conn.info.backend_pid;waiting.set()
        return live(conn,*args)
    with monkeypatch.context() as patch:
        if revocation_first:patch.setattr(service,'_live_context',observe_live)
        else:patch.setattr(repo,'append',pause_append)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(revoke) if revocation_first else pool.submit(service.record_send_boundary,lease=lease,expected_revision=lease.revision)
            try:
                assert held.wait(10)
                second=pool.submit(service.record_send_boundary,lease=lease,expected_revision=lease.revision) if revocation_first else pool.submit(revoke)
                assert waiting.wait(10);blocked_by(s,pids['waiter'],pids['holder'])
            finally:release.set()
            first.result(timeout=30)
            if revocation_first:
                with pytest.raises(repo.RecoveryConflict,match='REVOKED'):second.result(timeout=30)
            else:second.result(timeout=30)
    result=view(s)
    assert result.revoked and result.classification=='REVOKED_HELD'
    assert result.phase==('INTENT_RECORDED' if revocation_first else 'SEND_UNKNOWN')
    assert not result.execution_authorized and not result.provider_dispatched


def test_expired_checkpoint_cannot_gain_a_new_send_intent(checkpointed):
    s=checkpointed;a,r=new_linked_attempt(s,lease_seconds=1);check(s,handle=a.attempt_handle)
    wait_for_database_deadline(s,r)
    with pytest.raises(repo.RecoveryConflict,match='EXPIRED'):prepare(s,handle=a.attempt_handle)
    assert scalar(s,'SELECT count(*) FROM warehouse_query_send_intents WHERE attempt_id=%s',(a.attempt_id,))==0
    assert s.g.reservation(r)['status']=='EXECUTING'
