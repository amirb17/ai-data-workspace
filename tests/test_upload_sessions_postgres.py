"""Optional real PostgreSQL tests: RUN_DB_TESTS=1. S3 is mocked; no application tables are modified."""
import os
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
import psycopg
from psycopg import sql
from app.db import database, user_repository, workspace_repository, dataset_repository, file_repository, upload_session_repository
from app.services import upload_session_service as service
from app.services.dataset_service import get_or_create_dataset

@pytest.fixture
def context(monkeypatch):
    if os.getenv('RUN_DB_TESTS') != '1':
        pytest.skip('Set RUN_DB_TESTS=1 for isolated PostgreSQL integration tests')
    schema = 'test_phase4a_' + uuid4().hex
    # Connect through configured backend runtime without printing connection details.
    with database.get_connection() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    def connect():
        return psycopg.connect(database.DATABASE_URL, options=f'-c search_path={schema}')
    try:
        with connect() as conn:
            conn.execute(Path('migrations/0001_baseline.sql').read_text())
            conn.execute(Path('migrations/0007_identity_upload_sessions.sql').read_text())
        for module in (user_repository, workspace_repository, dataset_repository, file_repository, upload_session_repository):
            monkeypatch.setattr(module, 'get_connection', connect)
        user = user_repository.get_or_create_dev_user('dev:db-test','Test')
        workspace, _ = workspace_repository.create_workspace('Sales', owner=user['owner_key'])
        dataset = get_or_create_dataset(workspace[0], 'Orders', owner=user['owner_key'])
        objects = {}
        calls = {'hash':0,'promote':0,'delete':0}
        monkeypatch.setattr(service, 'generate_presigned_upload_url', lambda **kwargs: 'https://example.invalid/temporary')
        def metadata(key):
            if key not in objects: raise ValueError('Object not uploaded')
            return {'size':objects[key]['size'], 'content_type':'text/csv'}
        def digest(key):
            calls['hash'] += 1
            return objects[key]['hash']
        def promote(**kwargs):
            calls['promote'] += 1
            return 'raw/' + kwargs['file_hash'] + '/' + kwargs['file_name']
        def delete(key):
            calls['delete'] += 1
            objects.pop(key, None)
        monkeypatch.setattr(service,'get_object_metadata',metadata)
        monkeypatch.setattr(service,'calculate_s3_object_hash',digest)
        monkeypatch.setattr(service,'promote_staging_object_to_raw',promote)
        monkeypatch.setattr(service,'delete_s3_object',delete)
        yield connect, user, workspace, dataset, objects, calls
    finally:
        with database.get_connection() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def initiate(context, name='a.csv'):
    _, user, workspace, dataset, objects, _ = context
    response = service.initiate_upload_session(user['user_id'],workspace[0],dataset['dataset_id'],name,10,'text/csv')
    objects[response['object_key']] = {'size':10,'hash':'a'*64}
    return response


def test_postgres_identity_and_session_idempotency(context):
    connect, user, workspace, dataset, objects, calls = context
    assert user_repository.get_or_create_dev_user('dev:db-test','Test')['user_id'] == user['user_id']
    assert workspace_repository.list_workspaces_by_owner(user['owner_key'])[0][0] == workspace[0]
    assert dataset_repository.get_datasets_by_workspace(workspace[0])[0][0] == dataset['dataset_id']
    session = initiate(context)
    with connect() as conn:
        row = conn.execute('SELECT status, file_id FROM upload_requests WHERE upload_id=%s',(session['upload_request_id'],)).fetchone()
        assert row == ('INITIATED',None)
    result = service.complete_upload_session(session['upload_request_id'],user['user_id'])
    assert result['session_status'] == 'COMPLETED'
    assert result['upload_request']['upload_id'] == session['upload_request_id']
    assert session['object_key'] not in objects
    before = calls.copy()
    assert service.complete_upload_session(session['upload_request_id'],user['user_id']) == result
    assert calls == before
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM upload_requests').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM physical_files').fetchone()[0] == 1


def test_postgres_duplicate_physical_and_concurrent_complete(context):
    _, user, _, _, _, calls = context
    first = initiate(context)
    first_result = service.complete_upload_session(first['upload_request_id'],user['user_id'])
    second = initiate(context,'copy.csv')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.complete_upload_session(second['upload_request_id'],user['user_id']),range(2)))
    assert results[0] == results[1]
    assert results[0]['is_duplicate'] is True
    assert results[0]['file']['file_id'] == first_result['file']['file_id']
    assert calls['promote'] == 1
    with context[0]() as conn:
        assert conn.execute('SELECT COUNT(*) FROM upload_requests').fetchone()[0] == 2


def test_postgres_failure_recovers_and_ownership(context):
    connect, user, workspace, dataset, objects, _ = context
    session = initiate(context)
    objects[session['object_key']]['size'] = 9
    with pytest.raises(ValueError,match='size'):
        service.complete_upload_session(session['upload_request_id'],user['user_id'])
    with connect() as conn:
        assert conn.execute('SELECT status, file_id FROM upload_requests').fetchone() == ('FAILED', None)
    assert session['object_key'] in objects
    objects[session['object_key']]['size'] = 10
    assert service.complete_upload_session(session['upload_request_id'],user['user_id'])['session_status'] == 'COMPLETED'
    outsider = user_repository.get_or_create_dev_user('dev:other','Other')
    with pytest.raises(PermissionError):
        service.complete_upload_session(session['upload_request_id'],outsider['user_id'])
    with pytest.raises(ValueError,match='access denied'):
        service.initiate_upload_session(outsider['user_id'],workspace[0],dataset['dataset_id'],'a.csv',10)
    with pytest.raises(ValueError,match='does not belong'):
        service.initiate_upload_session(user['user_id'],workspace[0],dataset['dataset_id']+999,'a.csv',10)


def test_postgres_missing_object_and_commit_failure(context, monkeypatch):
    _, user, _, _, objects, _ = context
    session = initiate(context)
    original = objects.pop(session['object_key'])
    with pytest.raises(ValueError,match='not uploaded'):
        service.complete_upload_session(session['upload_request_id'],user['user_id'])
    objects[session['object_key']] = original
    complete = service.complete_session
    monkeypatch.setattr(service, 'complete_session', lambda *args: (_ for _ in ()).throw(RuntimeError('persist failure')))
    with pytest.raises(RuntimeError):
        service.complete_upload_session(session['upload_request_id'],user['user_id'])
    assert session['object_key'] in objects
    monkeypatch.setattr(service, 'complete_session', complete)
    assert service.complete_upload_session(session['upload_request_id'],user['user_id'])['session_status'] == 'COMPLETED'
    with context[0]() as conn:
        assert conn.execute('SELECT COUNT(*) FROM upload_requests').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM physical_files').fetchone()[0] == 1

def test_postgres_metadata_mismatch_and_cleanup_failure(context, monkeypatch):
    _, user, _, _, objects, calls = context
    session = initiate(context)
    metadata = service.get_object_metadata
    monkeypatch.setattr(service, 'get_object_metadata', lambda key: {'size':10, 'content_type':'application/octet-stream'})
    with pytest.raises(ValueError,match='content type'):
        service.complete_upload_session(session['upload_request_id'],user['user_id'])
    assert session['object_key'] in objects
    monkeypatch.setattr(service, 'get_object_metadata', metadata)
    monkeypatch.setattr(service, 'delete_s3_object', lambda key: (_ for _ in ()).throw(RuntimeError('cleanup failure')))
    result = service.complete_upload_session(session['upload_request_id'],user['user_id'])
    before = calls.copy()
    assert service.complete_upload_session(session['upload_request_id'],user['user_id']) == result
    assert calls == before
    assert result['session_status'] == 'COMPLETED'
