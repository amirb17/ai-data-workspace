"""Real job transactions in the established UUID-schema fixture; no AWS calls."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
from dataclasses import fields
from uuid import UUID, uuid4

import psycopg
import pytest

from app.db import database
from app.db import processing_job_repository as jobs
from test_rule_approval_postgres import pg


@pytest.fixture
def source(pg):
    connect, user, ws, ds, file, *_ = pg
    with connect() as conn:
        upload = conn.execute("""INSERT INTO upload_requests
            (user_id,workspace_id,dataset_id,file_id,status) VALUES (%s,%s,%s,%s,'UPLOADED')
            RETURNING upload_id""", (user['user_id'], ws, ds, file)).fetchone()[0]
    return connect, user['user_id'], upload


def admit(source, key='request-1', **kwargs):
    return jobs.create_job(source[1], source[2], 'CONTINUE', key, **kwargs)


def expire(source, job):
    # Deterministic database clock boundary; no sleep or production data changes.
    with source[0]() as conn:
        conn.execute("""UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'
            WHERE job_id=%s""", (job['job_id'],))


def test_idempotent_concurrent_admission_and_key_conflict(source):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: Context().run(admit, source), range(2)))
    a, b = results
    assert isinstance(a['job_id'], UUID) and a == b
    assert a['status'] == 'QUEUED' and a['attempt_count'] == 0
    with pytest.raises(jobs.IdempotencyConflict):
        admit(source, max_attempts=2)
    with source[0]() as conn:
        other = conn.execute('''INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status)
            SELECT user_id,workspace_id,dataset_id,file_id,status FROM upload_requests WHERE upload_id=%s
            RETURNING upload_id''', (source[2],)).fetchone()[0]
    with pytest.raises(jobs.IdempotencyConflict):
        jobs.create_job(source[1], other, 'CONTINUE', 'request-1')
    assert jobs.create_job(source[1], other, 'BRONZE', 'request-1')['job_id'] != a['job_id']
    with source[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_jobs').fetchone()[0] == 2


@pytest.mark.parametrize('invalid', ['stranger', 'incomplete', 'missing_file', 'archived', 'disabled',
                                   'wrong_workspace', 'wrong_dataset_owner', 'unfinished_session', 'missing'])
def test_reject_ineligible_admission(source, invalid):
    connect, user, upload = source
    with connect() as conn:
        if invalid == 'stranger':
            user = conn.execute("INSERT INTO users(owner_key,display_name) VALUES ('dev:stranger','Other') RETURNING user_id").fetchone()[0]
        elif invalid == 'incomplete':
            conn.execute("UPDATE upload_requests SET status='UPLOADING' WHERE upload_id=%s", (upload,))
        elif invalid == 'missing_file':
            conn.execute('UPDATE upload_requests SET file_id=NULL WHERE upload_id=%s', (upload,))
        elif invalid == 'archived':
            conn.execute('UPDATE upload_requests SET archived_at=now(),archived_by=%s WHERE upload_id=%s', (user, upload))
        elif invalid == 'disabled':
            conn.execute("UPDATE users SET account_status='DISABLED' WHERE user_id=%s", (user,))
        elif invalid == 'wrong_workspace':
            conn.execute('UPDATE upload_requests SET workspace_id=NULL WHERE upload_id=%s', (upload,))
        elif invalid == 'wrong_dataset_owner':
            conn.execute("UPDATE datasets SET owner='dev:other'")
        elif invalid == 'unfinished_session':
            conn.execute("""UPDATE upload_requests SET staging_object_key='isolated-only',
                source_file_name='a.csv',expected_file_size=10 WHERE upload_id=%s""", (upload,))
        else:
            upload = -1
    with pytest.raises(PermissionError):
        jobs.create_job(user, upload, 'CONTINUE', 'request')
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM processing_jobs').fetchone()[0] == 0


def test_current_owner_not_original_uploader(source):
    with source[0]() as conn:
        conn.execute('UPDATE upload_requests SET user_id=-1 WHERE upload_id=%s', (source[2],))
    assert admit(source)['requested_by'] == source[1]


def test_duplicate_claim_and_terminal_fencing(source):
    job = admit(source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: Context().run(jobs.claim_job, job['job_id']), range(2)))
    assert sum(c is not None for c in claims) == 1
    claimed = next(c for c in claims if c)
    assert claimed['attempt_count'] == 1
    assert jobs.claim_job(job['job_id']) is None
    done = jobs.finish_job(job['job_id'], claimed['lease_token'])
    assert done['status'] == 'SUCCEEDED' and done['completed_at'] is not None
    assert done['result_code'] == 'COMPLETED' and done['lease_token'] is None
    assert jobs.claim_job(job['job_id']) is None
    assert jobs.finish_job(job['job_id'], claimed['lease_token'], failure_code='PROCESSING_FAILED') is None
    assert admit(source) == done


def test_claim_skips_row_owned_by_competing_transaction(source):
    job = admit(source)
    with source[0]() as holder:
        holder.execute('SELECT job_id FROM processing_jobs WHERE job_id=%s FOR UPDATE', (job['job_id'],))
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(Context().run, jobs.claim_job, job['job_id']).result(timeout=30) is None
        assert holder.execute('SELECT attempt_count FROM processing_jobs').fetchone()[0] == 0
    assert jobs.claim_job(job['job_id'])['attempt_count'] == 1


def test_expired_lease_recovery_fences_old_worker(source):
    job = admit(source)
    first = jobs.claim_job(job['job_id'])
    expire(source, job)
    assert jobs.finish_job(job['job_id'], first['lease_token']) is None
    second = jobs.claim_job(job['job_id'])
    assert second['lease_token'] != first['lease_token']
    assert second['attempt_count'] == 2 and second['recovery_count'] == 1
    assert second['failure_code'] == 'LEASE_EXPIRED'
    assert jobs.finish_job(job['job_id'], first['lease_token']) is None
    assert jobs.finish_job(job['job_id'], second['lease_token'])['status'] == 'SUCCEEDED'


def test_exhausted_lease_is_terminal(source):
    job = admit(source, max_attempts=1)
    jobs.claim_job(job['job_id'])
    expire(source, job)
    assert jobs.claim_job(job['job_id']) is None
    with source[0]() as conn:
        assert conn.execute('SELECT status,failure_code,completed_at IS NOT NULL,lease_token FROM processing_jobs').fetchone() == ('FAILED', 'LEASE_EXPIRED', True, None)
    assert jobs.claim_job(job['job_id']) is None


@pytest.mark.parametrize('change', ['owner', 'archive', 'file', 'dataset'])
def test_claim_rechecks_authority_and_pinned_source(source, change):
    job = admit(source)
    with source[0]() as conn:
        if change == 'owner':
            conn.execute("UPDATE workspaces SET owner='dev:new-owner'")
        elif change == 'archive':
            conn.execute('UPDATE upload_requests SET archived_at=now(),archived_by=%s', (source[1],))
        elif change == 'file':
            other = conn.execute("""INSERT INTO physical_files(file_name,file_size,file_hash,storage_path,status)
                VALUES ('other.csv',10,%s,'test-only','UPLOADED') RETURNING file_id""", ('c'*64,)).fetchone()[0]
            conn.execute('UPDATE upload_requests SET file_id=%s', (other,))
        else:
            other = conn.execute("""INSERT INTO datasets(workspace_id,dataset_name,owner)
                SELECT workspace_id,'Other','dev:test' FROM workspaces RETURNING dataset_id""").fetchone()[0]
            conn.execute('UPDATE upload_requests SET dataset_id=%s', (other,))
    assert jobs.claim_job(job['job_id']) is None
    with source[0]() as conn:
        assert conn.execute('SELECT status,failure_code,attempt_count FROM processing_jobs').fetchone() == ('FAILED', 'SOURCE_INELIGIBLE', 0)


def test_retry_due_time_budget_and_safe_failure(source):
    job = admit(source, max_attempts=2)
    first = jobs.claim_job(job['job_id'])
    retry = jobs.finish_job(job['job_id'], first['lease_token'], failure_code='PROCESSING_FAILED', retry=True, retry_delay_seconds=60)
    assert retry['status'] == 'RETRY_WAIT' and retry['completed_at'] is None
    assert jobs.claim_job(job['job_id']) is None
    with source[0]() as conn:
        conn.execute("UPDATE processing_jobs SET available_at=clock_timestamp()-interval '1 second'")
    second = jobs.claim_job(job['job_id'])
    with pytest.raises(ValueError):
        jobs.finish_job(job['job_id'], second['lease_token'], failure_code='secret exception content')
    failed = jobs.finish_job(job['job_id'], second['lease_token'], failure_code='WORKER_INTERRUPTED', retry=True)
    assert failed['status'] == 'FAILED' and failed['attempt_count'] == 2
    assert failed['result_code'] is None and failed['completed_at'] is not None
    assert jobs.claim_job(job['job_id']) is None


def test_outer_transaction_rollback_for_admission_claim_and_completion(source):
    with pytest.raises(RuntimeError, match='rollback'):
        with database.repository_transaction():
            admit(source)
            raise RuntimeError('rollback')
    with source[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_jobs').fetchone()[0] == 0
    job = admit(source)
    with pytest.raises(RuntimeError, match='rollback'):
        with database.repository_transaction():
            assert jobs.claim_job(job['job_id'])['attempt_count'] == 1
            raise RuntimeError('rollback')
    claimed = jobs.claim_job(job['job_id'])
    assert claimed['attempt_count'] == 1
    with pytest.raises(RuntimeError, match='rollback'):
        with database.repository_transaction():
            assert jobs.finish_job(job['job_id'], claimed['lease_token'])['status'] == 'SUCCEEDED'
            raise RuntimeError('rollback')
    assert jobs.finish_job(job['job_id'], claimed['lease_token'])['status'] == 'SUCCEEDED'


def test_database_error_rolls_back_admission_and_transaction_stays_usable(source):
    with database.repository_transaction() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                job = admit(source)
                conn.execute('UPDATE processing_jobs SET attempt_count=-1 WHERE job_id=%s', (job['job_id'],))
        assert conn.execute('SELECT count(*) FROM processing_jobs').fetchone()[0] == 0
        assert admit(source)['status'] == 'QUEUED'
    with source[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_jobs').fetchone()[0] == 1


def test_completed_staged_session_is_eligible(source):
    with source[0]() as conn:
        conn.execute("""UPDATE upload_requests SET staging_object_key='test-only-completed',
            source_file_name='a.csv',expected_file_size=10,completed_at=now(),completion_result='{}'::jsonb""")
    assert admit(source)['status'] == 'QUEUED'


@pytest.mark.parametrize('kwargs', [dict(operation='ARBITRARY'), dict(idempotency_key=''),
                                   dict(idempotency_key='x'*129), dict(max_attempts=0)])
def test_invalid_admission_parameters_do_not_access_database(monkeypatch, kwargs):
    monkeypatch.setattr(jobs, 'get_connection', lambda: pytest.fail('Invalid request must not access DB'))
    values = dict(user_id=1, upload_id=1, operation='CONTINUE', idempotency_key='key')
    values.update(kwargs)
    with pytest.raises(ValueError):
        jobs.create_job(**values)


def test_claim_result_missing(source):
    assert jobs.claim_job_result(uuid4()) == jobs.ClaimResult(jobs.ClaimOutcome.NOT_FOUND)


def test_claim_result_minimal_lease_busy_and_success(source):
    job = admit(source)
    result = jobs.claim_job_result(job['job_id'])
    assert result.outcome == jobs.ClaimOutcome.CLAIMED and result.retry_at is None
    assert {f.name for f in fields(result)} == {'outcome', 'lease', 'retry_at'}
    lease = result.lease
    assert lease.job_id == job['job_id'] and lease.attempt_count == 1
    assert {f.name for f in fields(lease)} == {'job_id', 'lease_token', 'lease_expires_at', 'attempt_count'}
    assert str(lease.lease_token) not in repr(result)
    assert jobs.claim_job(job['job_id']) is None  # Legacy and typed APIs share the same lease.
    assert jobs.claim_job_result(job['job_id']) == jobs.ClaimResult(
        jobs.ClaimOutcome.BUSY, retry_at=lease.lease_expires_at)
    jobs.finish_job(job['job_id'], lease.lease_token)
    # Completed success needs no source access or ownership data in its result.
    with source[0]() as conn:
        conn.execute("UPDATE workspaces SET owner='dev:new-owner'")
    assert jobs.claim_job_result(job['job_id']) == jobs.ClaimResult(jobs.ClaimOutcome.ALREADY_SUCCEEDED)


@pytest.mark.parametrize('terminal', ['failed', 'exhausted', 'cancelled'])
def test_claim_result_terminal_failure(source, terminal):
    job = admit(source, max_attempts=1)
    if terminal == 'cancelled':
        with source[0]() as conn:
            conn.execute("UPDATE processing_jobs SET status='CANCELLED',completed_at=clock_timestamp()")
    else:
        lease = jobs.claim_job_result(job['job_id']).lease
        if terminal == 'failed':
            jobs.finish_job(job['job_id'], lease.lease_token, failure_code='PROCESSING_FAILED')
        else:
            expire(source, job)
    for _ in range(2):
        assert jobs.claim_job_result(job['job_id']) == jobs.ClaimResult(jobs.ClaimOutcome.TERMINAL_FAILED)
    assert jobs.claim_job(job['job_id']) is None


@pytest.mark.parametrize('status', ['QUEUED', 'RETRY_WAIT'])
def test_claim_result_not_due(source, status):
    job = admit(source)
    if status == 'RETRY_WAIT':
        lease = jobs.claim_job_result(job['job_id']).lease
        jobs.finish_job(job['job_id'], lease.lease_token, failure_code='PROCESSING_FAILED',
                        retry=True, retry_delay_seconds=60)
    else:
        with source[0]() as conn:
            conn.execute("UPDATE processing_jobs SET available_at=clock_timestamp()+interval '1 minute'")
    with source[0]() as conn:
        expected = conn.execute('SELECT available_at,attempt_count FROM processing_jobs').fetchone()
    assert jobs.claim_job_result(job['job_id']) == jobs.ClaimResult(jobs.ClaimOutcome.NOT_DUE, retry_at=expected[0])
    with source[0]() as conn:
        assert conn.execute('SELECT attempt_count FROM processing_jobs').fetchone()[0] == expected[1]


def test_claim_result_locked_row_is_busy_not_missing(source):
    job = admit(source)
    with source[0]() as holder:
        holder.execute('SELECT job_id FROM processing_jobs WHERE job_id=%s FOR UPDATE', (job['job_id'],))
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(Context().run, jobs.claim_job_result, job['job_id']).result(timeout=30)
        assert result == jobs.ClaimResult(jobs.ClaimOutcome.BUSY)
        assert holder.execute('SELECT attempt_count FROM processing_jobs').fetchone()[0] == 0
    assert jobs.claim_job_result(job['job_id']).outcome == jobs.ClaimOutcome.CLAIMED


@pytest.mark.parametrize('reclaim', [False, True])
def test_claim_results_concurrent_workers_and_recovery(source, reclaim):
    job = admit(source)
    old = None
    if reclaim:
        old = jobs.claim_job_result(job['job_id']).lease
        expire(source, job)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: Context().run(jobs.claim_job_result, job['job_id']), range(2)))
    assert sorted(r.outcome for r in results) == sorted([jobs.ClaimOutcome.CLAIMED, jobs.ClaimOutcome.BUSY])
    lease = next(r.lease for r in results if r.outcome == jobs.ClaimOutcome.CLAIMED)
    assert lease.attempt_count == (2 if reclaim else 1)
    if old:
        assert lease.lease_token != old.lease_token
        assert jobs.finish_job(job['job_id'], old.lease_token) is None
        assert jobs.finish_job(job['job_id'], old.lease_token, failure_code='PROCESSING_FAILED', retry=True) is None
    with source[0]() as conn:
        assert conn.execute('SELECT recovery_count,lease_token FROM processing_jobs').fetchone() == (int(reclaim), lease.lease_token)
    assert jobs.finish_job(job['job_id'], lease.lease_token)['status'] == 'SUCCEEDED'


@pytest.mark.parametrize('change', ['workspace_owner', 'dataset_owner', 'disabled', 'archive', 'expired_owner'])
def test_claim_result_source_ineligible_is_durable(source, change):
    job = admit(source)
    if change == 'expired_owner':
        active = jobs.claim_job_result(job['job_id'])
    with source[0]() as conn:
        if change in {'workspace_owner', 'expired_owner'}:
            conn.execute("UPDATE workspaces SET owner='dev:new-owner'")
        elif change == 'dataset_owner':
            conn.execute("UPDATE datasets SET owner='dev:new-owner'")
        elif change == 'disabled':
            conn.execute("UPDATE users SET account_status='DISABLED'")
        else:
            conn.execute('UPDATE upload_requests SET archived_at=clock_timestamp(),archived_by=%s', (source[1],))
    if change == 'expired_owner':
        # An active lease remains BUSY; claim resolution never authorizes its
        # execution. Revalidation occurs when recovery can actually claim it.
        assert jobs.claim_job_result(job['job_id']) == jobs.ClaimResult(
            jobs.ClaimOutcome.BUSY, retry_at=active.lease.lease_expires_at)
        expire(source, job)
    for _ in range(2):
        assert jobs.claim_job_result(job['job_id']) == jobs.ClaimResult(jobs.ClaimOutcome.SOURCE_INELIGIBLE)
    with source[0]() as conn:
        assert conn.execute('SELECT status,failure_code,lease_token,completed_at IS NOT NULL FROM processing_jobs').fetchone() == ('FAILED', 'SOURCE_INELIGIBLE', None, True)


def test_claim_result_rollback_and_database_error(source, monkeypatch):
    job = admit(source)
    with pytest.raises(RuntimeError, match='rollback'):
        with database.repository_transaction():
            lease = jobs.claim_job_result(job['job_id']).lease
            raise RuntimeError('rollback')
    assert jobs.finish_job(job['job_id'], lease.lease_token) is None
    def database_failure(conn, *_):
        conn.execute('SELECT 1/0')
    with monkeypatch.context() as patch:
        patch.setattr(jobs, '_source', database_failure)
        with pytest.raises(psycopg.errors.DivisionByZero):
            jobs.claim_job_result(job['job_id'])
    result = jobs.claim_job_result(job['job_id'])
    assert result.outcome == jobs.ClaimOutcome.CLAIMED and result.lease.attempt_count == 1


def worker_event(job_id, message_id='message'):
    import json
    return {'Records': [{'messageId': message_id, 'eventSource': 'aws:sqs',
                         'body': json.dumps({'job_id': str(job_id)})}]}


def test_worker_injected_success_and_duplicate_are_durable(source, monkeypatch):
    from app.lambda_worker import handler, service
    job = admit(source)
    executions = []
    monkeypatch.setattr(handler, 'execute_job', lambda job_id: service.execute_job(
        job_id, executor=lambda lease: executions.append(lease.job_id)))
    for _ in range(2):
        assert handler.lambda_handler(worker_event(job['job_id']), None) == {'batchItemFailures': []}
    assert executions == [job['job_id']]
    with source[0]() as conn:
        assert conn.execute('SELECT status,attempt_count,result_code,completed_at IS NOT NULL FROM processing_jobs').fetchone() == ('SUCCEEDED', 1, 'COMPLETED', True)


def test_worker_default_is_disabled_and_delivery_is_not_acked(source):
    from app.lambda_worker import handler
    job = admit(source)
    assert handler.lambda_handler(worker_event(job['job_id']), None) == {
        'batchItemFailures': [{'itemIdentifier': 'message'}]}
    with source[0]() as conn:
        assert conn.execute('SELECT status,failure_code,result_code FROM processing_jobs').fetchone() == ('RETRY_WAIT', 'PROCESSING_FAILED', None)


def test_worker_execution_failure_and_not_due_both_retry(source, monkeypatch):
    from app.lambda_worker import handler, service
    job = admit(source)
    calls = []
    def fail(lease):
        calls.append(lease.job_id)
        raise RuntimeError('Injected execution failure')
    monkeypatch.setattr(handler, 'execute_job', lambda job_id: service.execute_job(job_id, executor=fail))
    for _ in range(2):
        assert handler.lambda_handler(worker_event(job['job_id']), None)['batchItemFailures'] == [{'itemIdentifier': 'message'}]
    assert calls == [job['job_id']]
    with source[0]() as conn:
        assert conn.execute('SELECT status,attempt_count,result_code FROM processing_jobs').fetchone() == ('RETRY_WAIT', 1, None)


@pytest.mark.parametrize('reclaim', [False, True])
def test_worker_expired_or_stale_completion_cannot_ack(source, monkeypatch, reclaim):
    from app.lambda_worker import handler, service
    job = admit(source)
    current = []
    def executor(lease):
        expire(source, job)
        current.append(jobs.claim_job_result(job['job_id']).lease if reclaim else lease)
    monkeypatch.setattr(handler, 'execute_job', lambda job_id: service.execute_job(job_id, executor=executor))
    assert handler.lambda_handler(worker_event(job['job_id']), None)['batchItemFailures'] == [{'itemIdentifier': 'message'}]
    with source[0]() as conn:
        assert conn.execute('SELECT status,lease_token,result_code,attempt_count FROM processing_jobs').fetchone() == ('RUNNING', current[0].lease_token, None, 2 if reclaim else 1)


def test_worker_source_rejection_ack_and_mixed_batch(source, monkeypatch):
    from app.lambda_worker import handler, service
    rejected = admit(source)
    with source[0]() as conn:
        conn.execute("UPDATE workspaces SET owner='dev:new-owner'")
    monkeypatch.setattr(handler, 'execute_job', lambda job_id: service.execute_job(
        job_id, executor=lambda _: pytest.fail('Unauthorized claim must not execute')))
    batch = worker_event(rejected['job_id'], 'rejected')
    batch['Records'] += worker_event(uuid4(), 'missing')['Records']
    batch['Records'].append({'messageId': 'malformed', 'eventSource': 'aws:sqs', 'body': '{}'})
    assert handler.lambda_handler(batch, None) == {'batchItemFailures': [
        {'itemIdentifier': 'missing'}, {'itemIdentifier': 'malformed'}]}
    with source[0]() as conn:
        assert conn.execute('SELECT status,failure_code FROM processing_jobs').fetchone() == ('FAILED', 'SOURCE_INELIGIBLE')


def test_worker_commits_outside_inherited_transaction(source, monkeypatch):
    from app.lambda_worker import handler, service
    job = admit(source)
    monkeypatch.setattr(handler, 'execute_job', lambda job_id: service.execute_job(job_id, executor=lambda _: None))
    with pytest.raises(RuntimeError, match='outer rollback'):
        with database.repository_transaction():
            assert handler.lambda_handler(worker_event(job['job_id']), None) == {'batchItemFailures': []}
            raise RuntimeError('outer rollback')
    with source[0]() as conn:
        assert conn.execute('SELECT status FROM processing_jobs').fetchone()[0] == 'SUCCEEDED'


def test_renewal_uses_db_clock_and_preserves_token_and_attempt(source):
    job = admit(source)
    lease = jobs.claim_job_result(job['job_id'], lease_seconds=30).lease
    renewed = jobs.renew_job_lease(job['job_id'], lease.lease_token, lease_seconds=120)
    assert (renewed.job_id, renewed.lease_token, renewed.attempt_count) == (lease.job_id, lease.lease_token, 1)
    assert renewed.lease_expires_at > lease.lease_expires_at
    with source[0]() as conn:
        assert conn.execute("""SELECT lease_expires_at>clock_timestamp()+interval '100 seconds',
            lease_expires_at<=clock_timestamp()+interval '120 seconds',recovery_count FROM processing_jobs""").fetchone() == (True, True, 0)
    assert jobs.get_execution_scope(job['job_id'], lease.lease_token, minimum_lease_ms=100000) is not None


@pytest.mark.parametrize('invalid', ['wrong', 'expired', 'superseded', 'terminal', 'missing'])
def test_rejected_renewal_cannot_resurrect_job(source, invalid):
    job = admit(source)
    lease = jobs.claim_job_result(job['job_id']).lease
    token = lease.lease_token
    job_id = job['job_id']
    if invalid == 'wrong':
        token = uuid4()
    elif invalid in {'expired', 'superseded'}:
        expire(source, job)
        if invalid == 'superseded':
            assert jobs.claim_job_result(job_id).outcome == jobs.ClaimOutcome.CLAIMED
    elif invalid == 'terminal':
        jobs.finish_job(job_id, token)
    else:
        job_id = uuid4()
    assert jobs.renew_job_lease(job_id, token) is None
    assert jobs.get_execution_scope(job_id, token) is None


def test_concurrent_renewals_and_claim_do_not_rotate_live_lease(source):
    job = admit(source)
    lease = jobs.claim_job_result(job['job_id']).lease
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(Context().run, jobs.renew_job_lease, job['job_id'], lease.lease_token,
                               lease_seconds=seconds) for seconds in (180, 240)]
        contender = pool.submit(Context().run, jobs.claim_job_result, job['job_id'])
        renewals = [f.result(timeout=30) for f in futures]
        assert contender.result(timeout=30).outcome == jobs.ClaimOutcome.BUSY
    assert all(r.lease_token == lease.lease_token and r.attempt_count == 1 for r in renewals)
    with source[0]() as conn:
        assert conn.execute('SELECT lease_expires_at FROM processing_jobs').fetchone()[0] == max(r.lease_expires_at for r in renewals)


def test_renewal_row_lock_excludes_claim_and_rollback_preserves_expiry(source):
    job = admit(source)
    lease = jobs.claim_job_result(job['job_id']).lease
    with pytest.raises(RuntimeError, match='rollback'):
        with database.repository_transaction():
            assert jobs.renew_job_lease(job['job_id'], lease.lease_token, lease_seconds=240) is not None
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(Context().run, jobs.claim_job_result, job['job_id']).result(timeout=30).outcome == jobs.ClaimOutcome.BUSY
            raise RuntimeError('rollback')
    with source[0]() as conn:
        assert conn.execute('SELECT lease_expires_at FROM processing_jobs').fetchone()[0] == lease.lease_expires_at


def test_execution_scope_rejects_revoked_ownership_and_short_lease(source):
    job = admit(source)
    lease = jobs.claim_job_result(job['job_id'], lease_seconds=30).lease
    assert jobs.get_execution_scope(job['job_id'], lease.lease_token, minimum_lease_ms=60000) is None
    with source[0]() as conn:
        conn.execute("UPDATE datasets SET owner='dev:new-owner'")
    assert jobs.get_execution_scope(job['job_id'], lease.lease_token) is None


def test_boundary_holds_existing_locks_without_executing_processing(source):
    from app.lambda_worker.execution_boundary import ExecutionBoundary, ExecutionStopped
    job = admit(source)
    lease = jobs.claim_job_result(job['job_id']).lease
    boundary = ExecutionBoundary(lease, lambda: 300000)
    with pytest.raises(ExecutionStopped):
        with boundary.stage(required_ms=1000) as scope:
            with source[0]() as conn:
                schema = conn.execute('SELECT current_schema()').fetchone()[0]
                association = conn.execute('SELECT dataset_version_file_id FROM dataset_version_files').fetchone()[0]
                keys = (f'{schema}:datarise:dataset:{scope.workspace_id}:{scope.dataset_id}',
                        f'{schema}:datarise:upload-lifecycle:{scope.upload_id}',
                        f'{schema}:datarise:delivery:{association}')
                for key in keys:
                    assert not conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (key,)).fetchone()[0]
            expire(source, job)
            with pytest.raises(ExecutionStopped):
                boundary.checkpoint(renew=True)
    with source[0]() as conn:
        for key in keys:
            assert conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (key,)).fetchone()[0]
        assert conn.execute('SELECT count(*) FROM processing_attempts').fetchone()[0] == 0


def test_controlled_worker_renews_under_locks_and_commits_completion(source):
    from app.lambda_worker import service
    job = admit(source)
    calls = []
    def execute(boundary):
        with boundary.stage(required_ms=1000) as scope:
            assert scope.upload_id == source[2]
            boundary.checkpoint(renew=True)
            calls.append(scope.upload_id)
    service.execute_job(str(job['job_id']), boundary_executor=execute, remaining_time_ms=lambda: 300000)
    assert calls == [source[2]]
    with source[0]() as conn:
        assert conn.execute('SELECT status,attempt_count,lease_token FROM processing_jobs').fetchone() == ('SUCCEEDED', 1, None)
        assert conn.execute('SELECT count(*) FROM processing_attempts').fetchone()[0] == 0


def test_serial_container_worker_acks_only_committed_completion_and_duplicate(source):
    import json
    from app.processing_worker.worker import QueueMessage, SerialWorker
    job = admit(source)
    deliveries = [QueueMessage(name, json.dumps({'job_id': str(job['job_id'])}), 'receipt-'+name)
                  for name in ('first', 'duplicate')]
    class Queue:
        def __init__(self):
            self.acks = []
            self.closed = False
        def receive_one(self, stop):
            if deliveries:
                return deliveries.pop(0)
            stop.set()
        def acknowledge(self, message):
            with source[0]() as conn:
                assert conn.execute('SELECT status,attempt_count,completed_at IS NOT NULL FROM processing_jobs').fetchone() == ('SUCCEEDED', 1, True)
            self.acks.append(message.message_id)
        def retry(self, message):
            pytest.fail('Successful synthetic execution must not defer')
        def close(self):
            self.closed = True
    queue = Queue()
    executions = []
    SerialWorker(queue, boundary_executor=lambda boundary: executions.append(boundary.lease.job_id)).run()
    assert queue.acks == ['first', 'duplicate'] and queue.closed
    assert executions == [job['job_id']]
    with source[0]() as conn:
        assert conn.execute('SELECT count(*) FROM processing_attempts').fetchone()[0] == 0
