"""414E3 isolated PG, genuine sessions, exact policy and fake lookup-only SDK."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event, Lock, current_thread
from types import MappingProxyType, SimpleNamespace
from uuid import uuid4
import os

from fastapi import HTTPException
from google.api_core.exceptions import Forbidden, NotFound
from google.cloud.bigquery.job import QueryJob
from psycopg.rows import dict_row
import psycopg
import pytest

from app.auth import service as auth
from app.db.database import repository_transaction
from app.services import warehouse_sql_provider_policy_service as service
from app.services import warehouse_sql_recovery_attestation_service as attestation
from app.services import warehouse_sql_send_recovery_service as recovery
from app.warehouse.query_provider_policy import ProviderPolicy
from app.warehouse.query_recovery_attestation import digest
from tests.test_warehouse_sql_recovery_attestation_414e2 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query, served, checkpointed, prepared,
    lookup, CREATOR, scalar, state, view, change, wait_lease,
)
from tests.test_warehouse_sql_dispatch_checkpoint_414d5 import request, blocked_by
from app.db import warehouse_query_send_repository as repo

ROOT = Path(__file__).resolve().parents[1]


def prepare(s, token=None):
    return service.prepare_authenticated_provider_policy(request=request(s.token if token is None else token),
        workspace_id=s.f.pg['workspace'], attempt_handle=s.a.attempt_handle)


def run(lease, revision=None):
    return service.lookup_with_provider_policy(lease=lease, expected_revision=lease.revision if revision is None else revision)


@pytest.fixture
def unbound(lookup, monkeypatch):
    assert os.getenv('RUN_DB_TESTS') == '1'
    s, lease, client = lookup
    client.project = 'synthetic-project'; client.location = 'asia-south1'
    with s.f.pg['connect']() as conn:
        conn.execute((ROOT/'migrations/0056_warehouse_query_provider_policies.sql').read_text(encoding='utf-8-sig'))
    monkeypatch.setattr(service, '_POLICIES', MappingProxyType({s.f.pg['workspace']:
        ProviderPolicy('synthetic-project', CREATOR, 'reviewed-e3-1')}))
    return s, lease, client


@pytest.fixture
def bound(unbound):
    s, _, _ = unbound
    prepare(s)
    return unbound


def test_authenticated_exact_policy_idempotency_and_checked_sdk_metadata(unbound, monkeypatch):
    s, lease, client = unbound
    first = prepare(s); again = prepare(s)
    assert asdict(first) == asdict(again) == dict(attempt_handle=s.a.attempt_handle, classification='POLICY_PINNED_NO_DISPATCH')
    assert not first.execution_authorized and not first.provider_dispatched and not client.calls
    original = client.get_job
    def hydrate(*args, **kwargs):
        return QueryJob.from_api_repr(original(*args, **kwargs)._properties, SimpleNamespace(project='synthetic-project'))
    monkeypatch.setattr(client, 'get_job', hydrate)
    result = run(lease)
    assert result.verdict == 'VERIFIED_METADATA' and result.observation == 'SUCCEEDED'
    assert not result.execution_authorized and not result.provider_dispatched
    assert scalar(s, 'SELECT count(*) FROM warehouse_query_provider_policies') == 1
    assert scalar(s, '''SELECT count(*) FROM warehouse_query_provider_policies p
        JOIN warehouse_query_recovery_attestations a ON a.attempt_id=p.attempt_id AND a.policy_sha256=p.expected_sha256''') == 1
    assert state(s) == 'EXECUTING' and scalar(s, 'SELECT count(*) FROM query_governance_usage') == 0
    assert scalar(s, 'SELECT warehouse_query_publication_dependency_present(%s,%s)', (s.f.resource,s.pub['publication_id']))


@pytest.mark.parametrize('bad', ['project','location','job','sql','dataset','creator','extra_label','missing_label',
    'lower_cap','higher_cap','lower_timeout','higher_timeout','missing_dry_run','missing_priority','missing_billing_cap','missing_creator','missing_refs'])
def test_provider_scope_and_exact_configuration_cannot_be_substituted(bound, bad):
    s, lease, client = bound; body = client.body; q = body['configuration']['query']; config = body['configuration']
    if bad == 'project': body['jobReference']['projectId'] = 'foreign-project'
    elif bad == 'location': body['jobReference']['location'] = 'US'
    elif bad == 'job': body['jobReference']['jobId'] = 'other-job'
    elif bad == 'sql': q['query'] += ' LIMIT 1'
    elif bad == 'dataset': body['statistics']['query']['referencedTables'][0]['datasetId'] = 'foreign_dataset'
    elif bad == 'creator': body['user_email'] = 'foreign@foreign-project.iam.gserviceaccount.com'
    elif bad == 'extra_label': config['labels']['unapproved'] = 'injected'
    elif bad == 'missing_label': config['labels'].pop('intent')
    elif bad == 'lower_cap':
        q['maximumBytesBilled'] = '1048576'; body['statistics']['query']['totalBytesBilled'] = '1048576'
    elif bad == 'higher_cap': q['maximumBytesBilled'] = str(1<<30)
    elif bad == 'lower_timeout': config['jobTimeoutMs'] = '30000'
    elif bad == 'higher_timeout': config['jobTimeoutMs'] = '120000'
    elif bad == 'missing_dry_run': config.pop('dryRun')
    elif bad == 'missing_priority': q.pop('priority')
    elif bad == 'missing_billing_cap': q.pop('maximumBytesBilled')
    elif bad == 'missing_creator': body.pop('user_email')
    elif bad == 'missing_refs': body['statistics']['query'].pop('referencedTables')
    result = run(lease)
    assert result.observation == 'UNCERTAIN' and result.verdict in ('REJECTED','UNCERTAIN')
    if bad in ('lower_cap','lower_timeout','extra_label','missing_dry_run'):
        assert result.reason == {'lower_cap':'EXACT_BILLING_POLICY_MISMATCH','lower_timeout':'EXACT_TIMEOUT_POLICY_MISMATCH',
            'extra_label':'EXACT_LABEL_POLICY_MISMATCH','missing_dry_run':'MISSING_EXACT_CONFIGURATION'}[bad]
    assert view(s).phase == 'INTENT_RECORDED' and state(s) == 'EXECUTING'
    assert scalar(s, "SELECT count(*) FROM warehouse_query_recovery_attestations WHERE verdict='VERIFIED_METADATA'") == 0


@pytest.mark.parametrize('which', ['revision','maximum_bytes','creator','project','timeout','lookup_revision','governance'])
def test_current_server_policy_drift_denies_lookup_and_rebinding(bound, monkeypatch, which):
    s, lease, client = bound; workspace = s.f.pg['workspace']; original = service._POLICIES[workspace]
    if which == 'lookup_revision':
        runtime = attestation._runtime_for('synthetic-project','asia-south1')
        monkeypatch.setattr(attestation, '_RUNTIMES', MappingProxyType({('synthetic-project','asia-south1'):
            replace(runtime, policy=replace(runtime.policy, revision='changed'))}))
    elif which == 'governance': monkeypatch.setenv('QUERY_WORKSPACE_CONCURRENCY', '3')
    else:
        delta = dict(revision='changed', maximum_bytes=128*1024*1024, creator_email='other@synthetic-project.iam.gserviceaccount.com',
            project='foreign-project', timeout_ms=30000)
        key = {'creator':'creator_email','project':'project','timeout':'timeout_ms'}.get(which,which)
        monkeypatch.setattr(service, '_POLICIES', MappingProxyType({workspace:replace(original, **{key:delta[key]})}))
    with pytest.raises((service.PolicyConflict,attestation.LookupUnavailable)): run(lease)
    with pytest.raises((service.PolicyConflict,attestation.LookupUnavailable)): prepare(s)
    assert not client.calls and view(s).revision == lease.revision
    assert scalar(s,'SELECT count(*) FROM warehouse_query_provider_policies') == 1


@pytest.mark.parametrize('missing', ['workspace_policy','lookup_runtime','pinned_policy'])
def test_missing_server_or_durable_configuration_fails_closed(unbound, monkeypatch, missing):
    s, lease, client = unbound
    if missing == 'workspace_policy': monkeypatch.setattr(service,'_POLICIES',MappingProxyType({}))
    elif missing == 'lookup_runtime': monkeypatch.setattr(attestation,'_RUNTIMES',MappingProxyType({}))
    with pytest.raises((service.PolicyConflict,attestation.LookupUnavailable)): run(lease)
    if missing != 'pinned_policy':
        with pytest.raises(attestation.LookupUnavailable): prepare(s)
    assert not client.calls and scalar(s,'SELECT count(*) FROM warehouse_query_provider_policies') == 0


@pytest.mark.parametrize('identity', ['missing','forged','foreign','same_user_new_session'])
def test_only_original_verified_server_session_can_prepare(unbound, identity):
    s, lease, client = unbound; token = '' if identity == 'missing' else 'forged-owner-key'
    if identity in ('foreign','same_user_new_session'):
        who = s.f.pg['other'] if identity == 'foreign' else s.f.pg['owner']
        with s.f.pg['connect']() as conn: token = auth.issue_session(conn.cursor(row_factory=dict_row), who['user_id'])
    with pytest.raises(HTTPException): prepare(s, token)
    assert not client.calls and scalar(s,'SELECT count(*) FROM warehouse_query_provider_policies') == 0


@pytest.mark.parametrize('kind', ['account','session','workspace','publication'])
def test_revoked_context_cannot_bind_new_provider_policy(unbound, kind):
    s, _, client = unbound; change(s,kind)
    with pytest.raises((HTTPException,ValueError)): prepare(s)
    assert not client.calls and scalar(s,'SELECT count(*) FROM warehouse_query_provider_policies') == 0


def test_caller_config_principal_sql_permits_and_lease_scope_are_not_authority(bound):
    s, lease, client = bound
    for field in ('owner_key','sql','query','policy','provider','configuration','permit','enabled'):
        with pytest.raises(TypeError):
            service.prepare_authenticated_provider_policy(request=request(s.token), workspace_id=s.f.pg['workspace'],
                attempt_handle=s.a.attempt_handle, **{field:True})
        with pytest.raises(TypeError):
            service.lookup_with_provider_policy(lease=lease, expected_revision=lease.revision, **{field:True})
    with pytest.raises(repo.RecoveryConflict): run(replace(lease,token=uuid4()))
    with pytest.raises(repo.RecoveryConflict): run(lease,lease.revision-1)
    assert not client.calls


def test_provider_permission_denied_then_retry_preserves_identity(bound):
    s, lease, client = bound; client.error = Forbidden('private diagnostic must not escape')
    denied = run(lease)
    assert denied.observation == 'UNCERTAIN' and denied.reason == 'LOOKUP_UNAVAILABLE'
    client.error = None
    success = run(lease,denied.revision)
    assert success.verdict == 'VERIFIED_METADATA' and success.recovery.job == lease.job
    with pytest.raises(repo.RecoveryConflict): run(lease,denied.revision)
    assert len(client.calls) == 2 and state(s) == 'EXECUTING'


@pytest.mark.parametrize('at', ['prepare','persist'])
def test_database_permission_error_rolls_back_without_ack_or_partial_evidence(unbound, monkeypatch, at):
    s, lease, client = unbound
    if at == 'persist': prepare(s)
    original = service._derive if at == 'prepare' else attestation._persist
    def denied(conn,*args,**kwargs):
        result = original(conn,*args,**kwargs)
        conn.execute("DO $$ BEGIN RAISE EXCEPTION 'Synthetic denied runtime privilege' USING ERRCODE='42501'; END $$")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(service if at == 'prepare' else attestation, '_derive' if at == 'prepare' else '_persist', denied)
        with pytest.raises(psycopg.errors.InsufficientPrivilege): prepare(s) if at == 'prepare' else run(lease)
    assert view(s).revision == lease.revision
    assert scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations') == 0
    assert scalar(s,'SELECT count(*) FROM warehouse_query_provider_policies') == (at == 'persist')
    if at == 'prepare': prepare(s)
    assert run(lease).verdict == 'VERIFIED_METADATA'


@pytest.mark.parametrize('change_kind', ['provider_policy','lookup_policy','account','publication'])
def test_policy_or_revocation_during_real_inflight_lookup(bound, monkeypatch, change_kind):
    s, lease, client = bound; entered, release = Event(),Event()
    def pause():
        entered.set()
        if not release.wait(30): raise TimeoutError('Release missing')
    client.before = pause
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run,lease)
        try:
            assert entered.wait(10)
            with s.f.pg['connect']() as conn:
                assert conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',('datarise-query-governance-global',)).fetchone()[0]
                conn.execute('SELECT attempt_id FROM warehouse_query_send_intents WHERE attempt_id=%s FOR UPDATE NOWAIT',(s.a.attempt_id,))
            if change_kind == 'provider_policy':
                old = service._POLICIES[s.f.pg['workspace']]
                monkeypatch.setattr(service,'_POLICIES',MappingProxyType({s.f.pg['workspace']:replace(old,revision='drift-during-get')}))
            elif change_kind == 'lookup_policy':
                old = attestation._runtime_for('synthetic-project','asia-south1')
                monkeypatch.setattr(attestation,'_RUNTIMES',MappingProxyType({('synthetic-project','asia-south1'):
                    replace(old,policy=replace(old.policy,revision='drift-during-get'))}))
            else: change(s,change_kind)
        finally: release.set()
        if change_kind.endswith('policy'):
            with pytest.raises(service.PolicyConflict): future.result(timeout=30)
            assert view(s).revision == lease.revision
            assert scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations') == 0
        else:
            result = future.result(timeout=30)
            assert result.verdict == 'VERIFIED_METADATA' and result.recovery.revoked
            assert result.recovery.classification == 'REVOKED_HELD'
    assert scalar(s,'SELECT warehouse_query_publication_dependency_present(%s,%s)',(s.f.resource,s.pub['publication_id']))


def test_duplicate_inflight_workers_cannot_both_attest_same_revision(bound):
    s, lease, client = bound; both,release = Event(),Event(); mutex=Lock(); count=[0]
    def pause():
        with mutex:
            count[0] += 1
            if count[0] == 2: both.set()
        if not release.wait(30): raise TimeoutError('Release missing')
    client.before = pause
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run,lease) for _ in range(2)]
        try: assert both.wait(10)
        finally: release.set()
        good=[];bad=[]
        for future in futures:
            try: good.append(future.result(timeout=30))
            except repo.RecoveryConflict as error: bad.append(str(error))
    assert len(good) == len(bad) == 1 and bad == ['STALE_RECOVERY_REVISION_INSPECT_ONLY']
    assert scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations') == 1


def test_simultaneous_preparations_serialize_on_actual_postgres_lock(unbound, monkeypatch):
    s, _, client = unbound; held,waiting,release = Event(),Event(),Event();pids={}
    derive,transaction = service._derive,repo.transaction
    def pause(conn,*args,**kwargs):
        if current_thread().name.startswith('first'):
            pids['holder']=conn.info.backend_pid;held.set()
            if not release.wait(30):raise TimeoutError('Release missing')
        return derive(conn,*args,**kwargs)
    @contextmanager
    def observed(workspace_id=None):
        if current_thread().name.startswith('second'):
            with repository_transaction() as conn:
                conn.execute("SET LOCAL lock_timeout='5s'");conn.execute("SET LOCAL statement_timeout='10s'")
                pids['waiter']=conn.info.backend_pid;waiting.set()
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('datarise-query-governance-global',))
                yield conn
        else:
            with transaction(workspace_id) as conn:yield conn
    with monkeypatch.context() as patch:
        patch.setattr(service,'_derive',pause);patch.setattr(repo,'transaction',observed)
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='first') as first,ThreadPoolExecutor(max_workers=1,thread_name_prefix='second') as second:
            a=first.submit(prepare,s)
            try:
                assert held.wait(10);b=second.submit(prepare,s)
                assert waiting.wait(10);blocked_by(s,pids['waiter'],pids['holder'])
            finally:release.set()
            assert a.result(timeout=30)==b.result(timeout=30)
    assert scalar(s,'SELECT count(*) FROM warehouse_query_provider_policies')==1 and not client.calls


def test_expected_policy_is_immutable_once_only_and_nested_calls_fail_closed(bound):
    s,lease,client=bound
    with s.f.pg['connect']() as conn:
        for sql in ('DELETE FROM warehouse_query_provider_policies',"UPDATE warehouse_query_provider_policies SET expected_sha256=repeat('a',64)",'TRUNCATE warehouse_query_provider_policies'):
            with pytest.raises(psycopg.DatabaseError):
                with conn.transaction():conn.execute(sql)
        with pytest.raises(psycopg.errors.DuplicateTable):
            with conn.transaction():conn.execute((ROOT/'migrations/0056_warehouse_query_provider_policies.sql').read_text(encoding='utf-8-sig'))
    with repository_transaction():
        with pytest.raises(RuntimeError,match='Nested'):run(lease)
    assert not client.calls and view(s).revision==lease.revision


@pytest.mark.parametrize('lookup',[1],indirect=True)
def test_lease_expiry_reclaim_does_not_rebind_policy_or_allow_old_response(unbound):
    s,lease,client=unbound
    prepare(s);wait_lease(s,lease)
    lease=recovery.acquire_recovery(attempt_id=s.a.attempt_id,expected_revision=lease.revision,lease_seconds=1)
    entered,release=Event(),Event()
    def pause():
        entered.set()
        if not release.wait(30):raise TimeoutError('Release missing')
    client.before=pause
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(run,lease)
        try:
            assert entered.wait(10);wait_lease(s,lease)
            newer=recovery.acquire_recovery(attempt_id=s.a.attempt_id,expected_revision=lease.revision)
        finally:release.set()
        with pytest.raises(repo.RecoveryConflict):future.result(timeout=30)
    assert scalar(s,'SELECT count(*) FROM warehouse_query_recovery_attestations')==0
    client.before=None
    assert run(newer).verdict=='VERIFIED_METADATA'
    assert scalar(s,'SELECT count(*) FROM warehouse_query_provider_policies')==1


@pytest.mark.parametrize('field,value',[('project','foreign-project'),('location','US'),('location',None)])
def test_lookup_client_default_scope_must_match_trusted_binding(unbound, field, value):
    s,lease,client=unbound
    setattr(client,field,value)
    with pytest.raises(service.PolicyConflict,match='CLIENT_SCOPE'):prepare(s)
    assert not client.calls and scalar(s,'SELECT count(*) FROM warehouse_query_provider_policies')==0
