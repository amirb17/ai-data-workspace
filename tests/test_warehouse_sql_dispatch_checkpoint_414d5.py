"""414D5 real session/PG checks and overlapping transactions; no analytical IO."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from threading import Event, current_thread
from time import monotonic
from types import SimpleNamespace
import os

from fastapi import HTTPException
import psycopg
import pytest
from starlette.requests import Request

from app.auth.security import digest
from app.auth import service as auth
from app.db import query_governance_repository as budgets
from app.query_governance.types import QueryEstimate
from app.services import warehouse_sql_dispatch_checkpoint_service as dispatch
from app.services.warehouse_query_attempt_service import create_or_resume_warehouse_attempt
from app.services.warehouse_sql_stored_draft_service import register_reviewed_logical_draft_without_dispatch
from tests.test_warehouse_sql_trusted_recompilation_414d4e import (
    pg,modeling_pg,fact_provider_pg,genuinely_served_query,
)
from tests.test_warehouse_serving_postgres import publication, row

ROOT=Path(__file__).resolve().parents[1]


def request(token):
    return Request(dict(type='http',method='POST',path='/internal-test-only',
        headers=[(b'cookie',f'datarise_session={token}'.encode())]))


@pytest.fixture
def served(genuinely_served_query,monkeypatch):
    assert os.getenv('RUN_DB_TESTS')=='1', 'RUN_DB_TESTS=1 required; skips are not acceptance'
    f,pub,receipt,governance,attempt,reservation,compiled=genuinely_served_query
    monkeypatch.setenv('AUTH_MODE','session');monkeypatch.setenv('AUTH_COOKIE_SECURE','false')
    monkeypatch.setenv('AUTH_SECRET','synthetic-dispatch-session-test-secret-414d5')
    with f.pg['connect']() as conn:
        conn.execute((ROOT/'migrations/0053_warehouse_query_dispatch_checkpoints.sql').read_text())
        from psycopg.rows import dict_row
        token=auth.issue_session(conn.cursor(row_factory=dict_row),f.pg['owner']['user_id'])
    assert auth.session(token)['owner_key']==f.pg['owner']['owner_key']
    return SimpleNamespace(f=f,pub=pub,receipt=receipt,g=governance,a=attempt,r=reservation,q=compiled,token=token)


def check(s,token=None,handle=None):
    return dispatch.checkpoint_authenticated_warehouse_dispatch(request=request(s.token if token is None else token),
        workspace_id=s.f.pg['workspace'],attempt_handle=handle or s.a.attempt_handle)


def state(s):
    return s.g.reservation(s.r)['status']


def scalar(s,sql,params=()):
    with s.f.pg['connect']() as conn:return conn.execute(sql,params).fetchone()[0]


def test_checkpoint_and_duplicate_hold_one_identity_without_provider_io(served):
    s=served;calls=len(s.f.cloud.calls)
    result=check(s)
    assert result.state=='CHECKPOINTED_NO_DISPATCH'
    assert not result.execution_authorized and not result.provider_dispatched
    assert set(asdict(result))=={'attempt_handle','state'}
    assert state(s)=='EXECUTING' and check(s).state=='RECOVERY_REQUIRED'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==1
    assert scalar(s,'SELECT count(*) FROM query_governance_usage')==0
    assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))
    assert len(s.f.cloud.calls)==calls


@pytest.mark.parametrize('identity',['missing','forged','foreign'])
def test_only_real_current_server_session_can_claim(served,identity):
    s=served;token='' if identity=='missing' else 'client-forged-owner-key'
    if identity=='foreign':
        from psycopg.rows import dict_row
        with s.f.pg['connect']() as conn:
            token=auth.issue_session(conn.cursor(row_factory=dict_row),s.f.pg['other']['user_id'])
    with pytest.raises(HTTPException) as error:check(s,token=token)
    assert error.value.status_code==(403 if identity=='foreign' else 401)
    assert state(s)=='ACTIVE' and scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==0


def change(s,kind,*,restore=False):
    f=s.f
    if kind=='publication':
        publication(f,[row('C',3)],key='advanced')
        return
    with f.pg['connect']() as conn:
        if kind=='session':conn.execute('UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE token_digest=%s',(digest(s.token),))
        elif kind=='account':conn.execute('UPDATE users SET account_status=%s WHERE user_id=%s',('ACTIVE' if restore else 'DISABLED',f.pg['owner']['user_id']))
        elif kind=='workspace':conn.execute('UPDATE workspaces SET owner=%s WHERE workspace_id=%s',(f.pg['owner']['owner_key'] if restore else f.pg['other']['owner_key'],f.pg['workspace']))
        elif kind=='source':conn.execute('UPDATE datasets SET owner=%s WHERE dataset_id=%s',(f.pg['owner']['owner_key'] if restore else f.pg['other']['owner_key'],f.pg['dataset']))
        elif kind=='model':conn.execute("UPDATE warehouse_models SET status='ARCHIVED',archived_at=clock_timestamp() WHERE warehouse_model_id=%s",(f.model,))


@pytest.mark.parametrize('kind',['session','account','workspace','source','model','publication'])
def test_revocation_or_stale_publication_before_claim_is_denied(served,kind):
    s=served;change(s,kind)
    with pytest.raises((HTTPException,ValueError)):check(s)
    # Ownership may be gone, so inspect the isolated row directly.
    assert scalar(s,'SELECT status FROM query_governance_reservations WHERE reservation_id=%s',(s.r,))=='ACTIVE'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==0


@pytest.mark.parametrize('kind',['session','account','workspace','source','model','publication'])
def test_post_commit_revocation_is_durable_and_never_releases_hold(served,kind):
    s=served;check(s);change(s,kind)
    assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_revocations WHERE attempt_id=%s',(s.a.attempt_id,))>=1
    assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))
    if kind in ('account','workspace','source'):
        change(s,kind,restore=True)
        assert check(s).state=='REVOKED_RECOVERY_REQUIRED'
    if kind=='publication':
        assert check(s).state=='REVOKED_RECOVERY_REQUIRED'
        with s.f.pg['connect']() as conn:
            with pytest.raises(psycopg.DatabaseError,match='dependency'):
                conn.execute('INSERT INTO warehouse_query_publication_retirement_fences(resource_id,publication_id) VALUES(%s,%s)',(s.f.resource,s.pub['publication_id']))


def test_interruption_after_claim_rolls_back_and_lost_commit_response_is_recovery_only(served,monkeypatch):
    s=served;claim=budgets.claim
    def crash(*args,**kwargs):
        claim(*args,**kwargs)
        raise RuntimeError('Synthetic pre-commit interruption')
    with monkeypatch.context() as patch:
        patch.setattr(budgets,'claim',crash)
        with pytest.raises(RuntimeError,match='pre-commit'):check(s)
    assert state(s)=='ACTIVE' and scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==0
    # A successful commit followed by a lost response must not create another claim.
    check(s)
    assert check(s).state=='RECOVERY_REQUIRED'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==1


def test_existing_uncheckpointed_claim_is_never_retroactively_authorized(served):
    s=served;s.g.claim(s.r)
    assert check(s).state=='RECOVERY_REQUIRED'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==0


def test_current_policy_is_rechecked_without_counting_reservation_twice(served,monkeypatch):
    s=served;monkeypatch.setenv('QUERY_AUTO_EXECUTE_BYTES','1048576')
    with pytest.raises(ValueError,match='BUDGET_REVALIDATION'):check(s)
    assert state(s)=='ACTIVE'


def new_linked_attempt(s,*,intent=None,rows=5,lease_seconds=120):
    from app.services.warehouse_sql_stored_draft_service import logical_draft_digest
    with s.f.pg['connect']() as conn:
        original=conn.execute('SELECT logical_intent_sha256 FROM warehouse_query_attempt_registry WHERE attempt_id=%s',(s.a.attempt_id,)).fetchone()[0]
    a=create_or_resume_warehouse_attempt(governance=s.g,logical_intent_sha256=original)
    register_reviewed_logical_draft_without_dispatch(governance=s.g,attempt_handle=a.attempt_handle,
        model_id=s.f.model,logical_sql=f'SELECT id FROM model_{s.f.model} LIMIT 5')
    with s.f.pg['connect']() as conn:
        conn.execute('INSERT INTO warehouse_query_attempt_publication_pins(attempt_id,resource_id,publication_id,publication_revision) SELECT %s,resource_id,publication_id,publication_revision FROM warehouse_query_attempt_publication_pins WHERE attempt_id=%s',(a.attempt_id,s.a.attempt_id))
    r=s.g.admit(consumer='SQL',provider='BIGQUERY',request_token=a.request_token,
        intent_sha256=intent or s.q.intent_sha256,estimate=QueryEstimate(10*1024*1024),requested_rows=rows,lease_seconds=lease_seconds)['reservation']['reservation_id']
    with s.f.pg['connect']() as conn:
        conn.execute('INSERT INTO warehouse_query_attempt_reservation_links(attempt_id,reservation_id) VALUES(%s,%s)',(a.attempt_id,r))
    return a,r


@pytest.mark.parametrize('mismatch',['intent','rows'])
def test_authoritative_compilation_and_reserved_rows_must_match(served,mismatch):
    s=served;a,r=new_linked_attempt(s,intent='f'*64 if mismatch=='intent' else None,rows=6 if mismatch=='rows' else 5)
    with pytest.raises(ValueError,match='INTENT_MISMATCH|BUDGET_REVALIDATION'):check(s,handle=a.attempt_handle)
    assert s.g.reservation(r)['status']=='ACTIVE'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==0


def blocked_by(s,waiter,holder):
    deadline=monotonic()+5
    with s.f.pg['connect']() as conn:
        while monotonic()<deadline:
            if conn.execute('SELECT %s=ANY(pg_blocking_pids(%s))',(holder,waiter)).fetchone()[0]:return
    pytest.fail('Competing backend did not demonstrably wait on the held authorization lock')


@pytest.mark.parametrize('competitor',['duplicate','account_revoke'])
def test_real_overlapping_claim_or_revocation_is_serialized(served,monkeypatch,competitor):
    s=served;held,release,waiting=Event(),Event(),Event();pids={}
    compile_=dispatch._recompile_in_transaction;lock=budgets._lock_scopes
    def pause(conn,*args):
        result=compile_(conn,*args);pids['holder']=conn.info.backend_pid;held.set()
        if not release.wait(30):raise TimeoutError('Test release missing')
        return result
    def locks(conn,*args):
        if current_thread().name.startswith('second'):
            pids['waiter']=conn.info.backend_pid;waiting.set()
        return lock(conn,*args)
    def revoke():
        with s.f.pg['connect']() as conn:
            pids['waiter']=conn.info.backend_pid;waiting.set()
            conn.execute("UPDATE users SET account_status='DISABLED' WHERE user_id=%s",(s.f.pg['owner']['user_id'],))
    with monkeypatch.context() as patch:
        patch.setattr(dispatch,'_recompile_in_transaction',pause);patch.setattr(budgets,'_lock_scopes',locks)
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='first') as first, ThreadPoolExecutor(max_workers=1,thread_name_prefix='second') as second:
            a=first.submit(check,s)
            try:
                assert held.wait(30)
                b=second.submit(check,s) if competitor=='duplicate' else second.submit(revoke)
                assert waiting.wait(10)
                blocked_by(s,pids['waiter'],pids['holder'])
            finally:release.set()
            assert a.result(timeout=30).state=='CHECKPOINTED_NO_DISPATCH'
            outcome=b.result(timeout=30)
            if competitor=='duplicate':assert outcome.state=='RECOVERY_REQUIRED'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==1
    if competitor=='account_revoke':assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_revocations')==1


def test_retention_survives_terminal_accounting_and_history_is_immutable(served):
    s=served;check(s);publication(s.f,[row('C',3)],key='after-checkpoint')
    s.g.fail(s.r,bytes_processed=0,bytes_billed=0,provider_job_id=None,duration_ms=1)
    assert state(s)=='FAILED'
    assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))
    with s.f.pg['connect']() as conn:
        for sql in ('DELETE FROM warehouse_query_dispatch_checkpoints','UPDATE warehouse_query_dispatch_checkpoints SET valid_until=clock_timestamp()',
                    'TRUNCATE warehouse_query_dispatch_checkpoints','DELETE FROM warehouse_query_dispatch_revocations','TRUNCATE warehouse_query_dispatch_revocations'):
            with pytest.raises(psycopg.DatabaseError):
                with conn.transaction():conn.execute(sql)
        with pytest.raises(psycopg.DatabaseError,match='dependency'):
            conn.execute('INSERT INTO warehouse_query_publication_retirement_fences(resource_id,publication_id) VALUES(%s,%s)',(s.f.resource,s.pub['publication_id']))


@pytest.mark.parametrize('expired',['absolute','idle'])
def test_expired_real_session_cannot_claim(served,expired):
    s=served
    with s.f.pg['connect']() as conn:
        if expired=='absolute':
            conn.execute("UPDATE auth_sessions SET expires_at=created_at+interval '1 microsecond' WHERE token_digest=%s",(digest(s.token),))
        else:
            conn.execute("UPDATE auth_sessions SET last_seen_at=clock_timestamp()-interval '31 minutes' WHERE token_digest=%s",(digest(s.token),))
    with pytest.raises(HTTPException) as error:check(s)
    assert error.value.status_code==401 and state(s)=='ACTIVE'


def wait_for_database_deadline(s,r):
    # Exercise a real one-second lease, not a fabricated expiry or sleep race.
    # Deadline-derived server wait is capped at two seconds, not indefinite.
    with s.f.pg['connect']() as conn:
        conn.execute("SELECT pg_sleep(LEAST(2,GREATEST(0,EXTRACT(epoch FROM expires_at-clock_timestamp()))+0.01)) FROM query_governance_reservations WHERE reservation_id=%s",(r,))


@pytest.mark.parametrize('claim_first',[False,True])
def test_real_lease_expiry_never_allows_a_new_claim_or_releases_checkpoint(served,claim_first):
    s=served;a,r=new_linked_attempt(s,lease_seconds=1)
    if claim_first:assert check(s,handle=a.attempt_handle).state=='CHECKPOINTED_NO_DISPATCH'
    wait_for_database_deadline(s,r)
    if claim_first:
        assert check(s,handle=a.attempt_handle).state=='EXPIRED_RECOVERY_REQUIRED'
        assert s.g.reservation(r)['status']=='EXECUTING'
        assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))
    else:
        with pytest.raises(ValueError,match='NOT_READY|EXPIRED'):check(s,handle=a.attempt_handle)
        assert s.g.reservation(r)['status']=='ACTIVE'
        assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==0


def test_caller_sql_principal_and_permit_are_not_accepted(served):
    s=served
    for field in ('owner_key','sql','permit','governance','provider','policy','enabled'):
        with pytest.raises(TypeError):
            dispatch.checkpoint_authenticated_warehouse_dispatch(request=request(s.token),workspace_id=s.f.pg['workspace'],
                attempt_handle=s.a.attempt_handle,**{field:'client-created'})
    assert state(s)=='ACTIVE'


def test_committing_revocation_wins_before_waiting_checkpoint_can_claim(served,monkeypatch):
    s=served;held,release,waiting=Event(),Event(),Event();pids={};session=dispatch._session_locked
    def revoke():
        with s.f.pg['connect']() as conn:
            conn.execute("UPDATE users SET account_status='DISABLED' WHERE user_id=%s",(s.f.pg['owner']['user_id'],))
            pids['holder']=conn.info.backend_pid;held.set()
            if not release.wait(30):raise TimeoutError('Revocation release missing')
    def authenticate(conn,*args):
        pids['waiter']=conn.info.backend_pid;waiting.set()
        return session(conn,*args)
    with monkeypatch.context() as patch:
        patch.setattr(dispatch,'_session_locked',authenticate)
        with ThreadPoolExecutor(max_workers=2) as pool:
            revoked=pool.submit(revoke)
            try:
                assert held.wait(10)
                attempted=pool.submit(check,s)
                assert waiting.wait(10)
                blocked_by(s,pids['waiter'],pids['holder'])
            finally:release.set()
            revoked.result(timeout=30)
            with pytest.raises(HTTPException) as error:attempted.result(timeout=30)
            assert error.value.status_code==401
    assert scalar(s,'SELECT status FROM query_governance_reservations WHERE reservation_id=%s',(s.r,))=='ACTIVE'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_dispatch_checkpoints')==0


@pytest.mark.parametrize('mode',['development','missing_secret'])
def test_session_configuration_cannot_be_bypassed(served,monkeypatch,mode):
    s=served
    if mode=='development':monkeypatch.setenv('AUTH_MODE','development')
    else:monkeypatch.delenv('AUTH_SECRET',raising=False)
    with pytest.raises(HTTPException) as error:check(s)
    assert error.value.status_code==503 and state(s)=='ACTIVE'


def test_outer_transaction_is_not_committed_and_migration_is_once_only(served):
    from app.db.database import repository_transaction
    s=served
    with repository_transaction():
        with pytest.raises(RuntimeError,match='Nested'):check(s)
    assert state(s)=='ACTIVE'
    with s.f.pg['connect']() as conn:
        with pytest.raises(psycopg.errors.DuplicateTable):
            with conn.transaction():conn.execute((ROOT/'migrations/0053_warehouse_query_dispatch_checkpoints.sql').read_text())
        assert conn.execute('SELECT count(*) FROM warehouse_query_dispatch_checkpoints').fetchone()[0]==0
