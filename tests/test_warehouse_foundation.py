"""11A metadata only: no cloud/storage/provider calls, isolated PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.db import database
from app.schemas.warehouse import (ModelCreate, VersionCreate, SourceMapping, ResourceRegistration,
                                   OperationCreate, RunTransition)
from app.services import warehouse_model_service as models
from app.services import warehouse_resource_service as resources
from app.services import warehouse_run_service as runs
from app.warehouse import config
from app.warehouse.provider import PLANNED_PROVIDER

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'migrations/0029_warehouse_foundation.sql'
ROLLBACK = ROOT / 'migrations/rollback/0029_warehouse_foundation.sql'
TABLES = ('warehouse_models', 'warehouse_model_versions', 'warehouse_model_sources',
          'warehouse_resources', 'warehouse_operations', 'warehouse_runs')


@pytest.fixture
def pg(monkeypatch):
    if os.getenv('RUN_DB_TESTS') != '1':
        pytest.skip('Set RUN_DB_TESTS=1 for isolated warehouse PostgreSQL tests')
    schema = 'test_warehouse_'+uuid.uuid4().hex
    original_url = database.DATABASE_URL
    with psycopg.connect(original_url) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    test_url = make_conninfo(original_url, options='-c search_path='+schema)
    monkeypatch.setattr(database, 'DATABASE_URL', test_url)
    monkeypatch.setenv('WAREHOUSE_ENABLED', 'true')
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('WAREHOUSE_PROVIDER', 'bigquery')

    def connect():
        return psycopg.connect(test_url)

    try:
        with connect() as conn:
            for migration in sorted((ROOT / 'migrations').glob('*.sql')):
                # This fixture verifies the 11A baseline; later migrations have
                # dedicated fixtures and must not run before 0029 exists.
                if migration.name < MIGRATION.name:
                    conn.execute(migration.read_text(encoding='utf-8'))
            conn.execute(MIGRATION.read_text(encoding='utf-8'))
            # Existing pending adoption migration is compatible too, without applying it to public.
            conn.execute((ROOT / 'migrations/pending/0028_ownership_adoption_events.sql').read_text(encoding='utf-8'))
            users = []
            for name in ('warehouse-owner', 'warehouse-other', 'warehouse-new-owner'):
                actor = conn.execute("INSERT INTO users(owner_key,display_name) VALUES (%s,%s) RETURNING user_id,owner_key", ('usr:'+name, name)).fetchone()
                users.append(dict(user_id=actor[0], owner_key=actor[1]))
            workspace = conn.execute("INSERT INTO workspaces(workspace_name,owner) VALUES ('Warehouse fixture',%s) RETURNING workspace_id", (users[0]['owner_key'],)).fetchone()[0]
            foreign_workspace = conn.execute("INSERT INTO workspaces(workspace_name,owner) VALUES ('Foreign fixture',%s) RETURNING workspace_id", (users[1]['owner_key'],)).fetchone()[0]
            dataset = conn.execute("INSERT INTO datasets(dataset_name,workspace_id,owner) VALUES ('Source',%s,%s) RETURNING dataset_id", (workspace, users[0]['owner_key'])).fetchone()[0]
            foreign_dataset = conn.execute("INSERT INTO datasets(dataset_name,workspace_id,owner) VALUES ('Foreign source',%s,%s) RETURNING dataset_id", (foreign_workspace, users[1]['owner_key'])).fetchone()[0]
        yield dict(connect=connect, workspace=workspace, foreign_workspace=foreign_workspace,
                   dataset=dataset, foreign_dataset=foreign_dataset, owner=users[0], other=users[1], successor=users[2])
    finally:
        # Identifier is generated here; never use a caller-provided/public schema.
        assert schema.startswith('test_warehouse_')
        with psycopg.connect(original_url) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def draft(pg, name='Orders'):
    w, u = pg['workspace'], pg['owner']
    m = models.create_model(w, u, ModelCreate(display_name=name))
    v = models.create_version(w, m['warehouse_model_id'], u, VersionCreate(expected_version=0))
    return m['warehouse_model_id'], v['warehouse_model_version_id']


def approved(pg, name='Orders'):
    m, v = draft(pg, name)
    models.add_source(pg['workspace'], m, v, pg['owner'], SourceMapping(source_dataset_id=pg['dataset']))
    models.approve_version(pg['workspace'], m, v, pg['owner'])
    return m, v


def resource(pg, m, v, table='synthetic_generation_1', generation=1):
    return resources.register_resource(pg['workspace'], m, v, pg['owner'], ResourceRegistration(
        environment='development', location='asia-south1', generation=generation,
        physical_reference=dict(project='synthetic-project', dataset='synthetic_dataset', table=table)))


def operation(pg):
    m, v = approved(pg)
    r = resource(pg, m, v)
    op = runs.create_operation(pg['workspace'], m, v, pg['owner'], OperationCreate(
        operation_type='INITIAL_LOAD', resource_id=r['resource_id'], reason='Synthetic metadata lifecycle'))
    return m, v, r, op


def test_disabled_default_does_not_access_database_or_require_provider(monkeypatch):
    monkeypatch.delenv('WAREHOUSE_ENABLED', raising=False)
    monkeypatch.setenv('WAREHOUSE_PROVIDER', 'invalid-while-disabled')
    monkeypatch.setenv('APP_ENV', 'invalid-while-disabled')
    assert not config.settings().enabled
    assert PLANNED_PROVIDER.identity == 'BIGQUERY' and not PLANNED_PROVIDER.supports_execution
    monkeypatch.setattr(models, 'repository_transaction', lambda: pytest.fail('Disabled service accessed database'))
    with pytest.raises(RuntimeError, match='disabled'):
        models.list_models(1, {'user_id': 1, 'owner_key': 'synthetic'})


@pytest.mark.parametrize('environment', ['development', 'production'])
def test_enabled_settings_use_application_environment(monkeypatch, environment):
    monkeypatch.setenv('WAREHOUSE_ENABLED', 'true')
    monkeypatch.setenv('APP_ENV', environment)
    monkeypatch.setenv('WAREHOUSE_PROVIDER', 'bigquery')
    assert config.settings().environment == environment
    assert config.settings().production_location == 'asia-south1'


@pytest.mark.parametrize('payload', [
    dict(display_name='Orders', table='injected'), dict(display_name='Orders', credentials={}),
    dict(display_name='Orders', project='injected'), dict(display_name='\x00invalid')])
def test_logical_input_rejects_physical_fields_and_control_characters(payload):
    with pytest.raises(ValidationError):
        ModelCreate(**payload)


def test_configuration_contract_is_versioned_and_strict():
    assert VersionCreate(expected_version=0).key_algorithm_version == 1
    for extra in (dict(key_algorithm_version=2), dict(configuration={'uncontrolled': True}), dict(scd_type='SCD_TYPE_2')):
        with pytest.raises(ValidationError):
            VersionCreate(expected_version=0, **extra)
    with pytest.raises(ValidationError):
        ResourceRegistration(environment='production', location='us-central1', generation=1,
                             physical_reference=dict(project='synthetic-project', dataset='d', table='t'))
    with pytest.raises(ValidationError):
        ResourceRegistration(environment='development', location='asia-south1', generation=1,
                             physical_reference=dict(project='synthetic-project', dataset='d', table='t', secret='no'))


def test_no_cloud_import_execution_hooks_or_api_routes():
    import ast
    paths = [*ROOT.joinpath('app/warehouse').glob('*.py'), ROOT/'app/schemas/warehouse.py',
             ROOT/'app/db/warehouse_repository.py', *ROOT.joinpath('app/services').glob('warehouse_*_service.py')]
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name.startswith(('google', 'boto3', 'duckdb')) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                # 11E verifies immutable accepted source outcomes by reusing
                # these pure engines in one bounded, read-only adapter.
                if path == ROOT/'app/warehouse/source_evidence.py' and node.module in (
                    'app.processing.append_engine', 'app.processing.upsert_engine',
                    'app.processing.snapshot_engine'):
                    continue
                assert not (node.module or '').startswith(('google', 'app.storage', 'app.processing'))
    for filename in ('append_application_service.py', 'incremental_application_service.py',
                     'delivery_execution_service.py', 'dataset_analytics_service.py'):
        tree = ast.parse((ROOT/'app/services'/filename).read_text(encoding='utf-8'))
        assert not any(isinstance(n, ast.ImportFrom) and (n.module or '').startswith('app.warehouse')
                       for n in ast.walk(tree))
    from app.main import app
    assert not any('warehouse' in path for path in app.openapi()['paths'])


def test_model_names_versions_draft_mapping_and_atomic_approval(pg):
    w, u = pg['workspace'], pg['owner']
    m, v1 = draft(pg, '  Orders  ')
    assert models.read_model(w, m, u)['normalized_name'] == 'orders'
    with pytest.raises(psycopg.errors.UniqueViolation):
        models.create_model(w, u, ModelCreate(display_name='ORDERS'))
    first = models.read_version(w, m, v1, u)
    assert first['version_number'] == first['key_algorithm_version'] == 1 and first['status'] == 'DRAFT'
    with pytest.raises(HTTPException):
        models.approve_version(w, m, v1, u)
    mapping = models.add_source(w, m, v1, u, SourceMapping(source_dataset_id=pg['dataset']))
    models.remove_source(w, m, v1, mapping['source_mapping_id'], u)
    models.add_source(w, m, v1, u, SourceMapping(source_dataset_id=pg['dataset']))
    models.approve_version(w, m, v1, u)
    v2 = models.create_version(w, m, u, VersionCreate(expected_version=1))['warehouse_model_version_id']
    assert models.read_version(w, m, v1, u)['status'] == 'APPROVED'
    with pytest.raises(ValueError):
        models.create_version(w, m, u, VersionCreate(expected_version=1))
    models.add_source(w, m, v2, u, SourceMapping(source_dataset_id=pg['dataset']))
    models.approve_version(w, m, v2, u)
    assert models.read_version(w, m, v1, u)['status'] == 'ARCHIVED'
    assert models.read_version(w, m, v2, u)['status'] == 'APPROVED'
    assert models.read_model(w, m, u)['key_namespace'] == models.read_model(w, m, u)['key_namespace']


def test_concurrent_version_creation_serializes_expected_version(pg):
    m, _ = draft(pg)
    def create():
        try:
            return models.create_version(pg['workspace'], m, pg['owner'], VersionCreate(expected_version=1))['version_number']
        except ValueError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert results.count(2) == results.count('conflict') == 1


@pytest.mark.parametrize('statement', [
    "UPDATE warehouse_model_versions SET key_algorithm_version=key_algorithm_version",
    "UPDATE warehouse_model_versions SET created_by=created_by",
    "DELETE FROM warehouse_model_versions",
    "DELETE FROM warehouse_model_sources",
    "TRUNCATE warehouse_model_sources",
    "UPDATE warehouse_models SET created_at=NOW()",
])
def test_database_protects_approved_identity_sources_and_actors(pg, statement):
    approved(pg)
    with pytest.raises(psycopg.Error), pg['connect']() as conn:
        conn.execute(statement)


def test_draft_configuration_also_requires_new_version(pg):
    draft(pg)
    with pytest.raises(psycopg.Error), pg['connect']() as conn:
        conn.execute("UPDATE warehouse_model_versions SET key_strategy='OTHER'")


def test_foreign_user_cannot_list_read_mutate_or_guess_ids(pg):
    m, v, r, op = operation(pg)
    w, u = pg['workspace'], pg['other']
    run = runs.new_attempt(w, op['operation_id'], pg['owner'])
    calls = [lambda: models.list_models(w, u), lambda: models.read_model(w, m, u),
             lambda: models.create_model(w, u, ModelCreate(display_name='Forbidden')),
             lambda: models.create_version(w, m, u, VersionCreate(expected_version=1)),
             lambda: models.read_version(w, m, v, u), lambda: models.archive_model(w, m, u),
             lambda: models.add_source(w, m, v, u, SourceMapping(source_dataset_id=pg['dataset'])),
             lambda: models.approve_version(w, m, v, u),
             lambda: models.remove_source(w, m, v, 999999, u),
             lambda: resources.list_resources(w, m, v, u), lambda: resources.read_resource(w, m, v, r['resource_id'], u),
             lambda: resources.register_resource(w, m, v, u, ResourceRegistration(environment='development', location='asia-south1', generation=2,
                 physical_reference=dict(project='synthetic-project', dataset='d', table='forbidden'))),
             lambda: runs.create_operation(w, m, v, u, OperationCreate(operation_type='REPLAY', resource_id=r['resource_id'], reason='Forbidden')),
             lambda: runs.read_operation(w, op['operation_id'], u),
             lambda: runs.read_run(w, op['operation_id'], run['warehouse_run_id'], u),
             lambda: runs.new_attempt(w, op['operation_id'], u),
             lambda: runs.record_transition(w, op['operation_id'], run['warehouse_run_id'], u, RunTransition(status='RUNNING'))]
    for call in calls:
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 403


def test_cross_model_version_resource_operation_ids_are_isolated(pg):
    m, v, r, op = operation(pg)
    other_m, other_v = approved(pg, 'Other model')
    w, u = pg['workspace'], pg['owner']
    for call in (lambda: models.read_version(w, other_m, v, u),
                 lambda: resources.read_resource(w, other_m, other_v, r['resource_id'], u),
                 lambda: runs.create_operation(w, other_m, other_v, u, OperationCreate(operation_type='REPLAY', resource_id=r['resource_id'], reason='Wrong model'))):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 404
    with pytest.raises(psycopg.Error), pg['connect']() as conn:
        conn.execute('''INSERT INTO warehouse_operations(workspace_id,warehouse_model_id,warehouse_model_version_id,
            resource_id,environment,operation_type,reason,created_by) VALUES (%s,%s,%s,%s,'development','REPLAY','Wrong model',%s)''',
            (w, other_m, other_v, r['resource_id'], u['user_id']))


def test_foreign_source_rejected_by_service_and_database(pg):
    m, v = draft(pg)
    with pytest.raises(HTTPException):
        models.add_source(pg['workspace'], m, v, pg['owner'], SourceMapping(source_dataset_id=pg['foreign_dataset']))
    with pytest.raises(psycopg.Error), pg['connect']() as conn:
        conn.execute('''INSERT INTO warehouse_model_sources(workspace_id,warehouse_model_id,warehouse_model_version_id,source_dataset_id,purpose)
            VALUES (%s,%s,%s,%s,'PRIMARY')''', (pg['workspace'], m, v, pg['foreign_dataset']))


def test_workspace_version_scope_and_approved_uniqueness_in_database(pg):
    m, v = approved(pg)
    w, u = pg['workspace'], pg['owner']
    next_v = models.create_version(w, m, u, VersionCreate(expected_version=1))['warehouse_model_version_id']
    models.add_source(w, m, next_v, u, SourceMapping(source_dataset_id=pg['dataset']))
    with pytest.raises(psycopg.errors.UniqueViolation), pg['connect']() as conn:
        conn.execute("UPDATE warehouse_model_versions SET status='APPROVED',reviewed_by=%s,reviewed_at=NOW() WHERE warehouse_model_version_id=%s", (u['user_id'], next_v))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), pg['connect']() as conn:
        conn.execute('''INSERT INTO warehouse_resources(workspace_id,warehouse_model_id,warehouse_model_version_id,
            provider,environment,location,generation,physical_reference,created_by)
            VALUES (%s,%s,%s,'BIGQUERY','development','asia-south1',1,
            '{"reference_version":1,"project":"synthetic-project","dataset":"d","table":"t"}',%s)''',
            (pg['foreign_workspace'], m, v, u['user_id']))
    with pytest.raises(HTTPException) as error:
        models.read_version(pg['foreign_workspace'], m, v, pg['other'])
    assert error.value.status_code == 404


def test_source_membership_drift_blocks_metadata_use(pg):
    m, v = approved(pg)
    with pg['connect']() as conn:
        conn.execute('UPDATE datasets SET workspace_id=%s WHERE dataset_id=%s', (pg['foreign_workspace'], pg['dataset']))
    with pytest.raises(HTTPException):
        models.read_version(pg['workspace'], m, v, pg['owner'])
    with pytest.raises(HTTPException):
        resource(pg, m, v)


def test_current_workspace_owner_authorizes_historical_actor_does_not(pg):
    m, v, r, op = operation(pg)
    w, old, current = pg['workspace'], pg['owner'], pg['successor']
    with pg['connect']() as conn:
        conn.execute('UPDATE workspaces SET owner=%s WHERE workspace_id=%s', (current['owner_key'], w))
        conn.execute('UPDATE datasets SET owner=%s WHERE dataset_id=%s', (current['owner_key'], pg['dataset']))
    assert models.read_model(w, m, current)['created_by'] == old['user_id']
    assert models.read_version(w, m, v, current)['reviewed_by'] == old['user_id']
    assert resources.read_resource(w, m, v, r['resource_id'], current)['created_by'] == old['user_id']
    assert runs.read_operation(w, op['operation_id'], current)['created_by'] == old['user_id']
    assert runs.new_attempt(w, op['operation_id'], current)['created_by'] == current['user_id']
    with pytest.raises(HTTPException):
        models.list_models(w, old)
    with pytest.raises(HTTPException):
        runs.read_operation(w, op['operation_id'], old)


def test_archived_workspace_and_model_are_owned_read_only(pg):
    m, _ = approved(pg)
    w, u = pg['workspace'], pg['owner']
    models.archive_model(w, m, u)
    assert models.read_model(w, m, u)['status'] == 'ARCHIVED'
    with pytest.raises(HTTPException):
        models.create_version(w, m, u, VersionCreate(expected_version=1))
    with pg['connect']() as conn:
        conn.execute("UPDATE workspaces SET status='ARCHIVED' WHERE workspace_id=%s", (w,))
    assert len(models.list_models(w, u)) == 1
    with pytest.raises(HTTPException):
        models.create_model(w, u, ModelCreate(display_name='Blocked'))
    with pytest.raises(HTTPException):
        models.list_models(w, pg['other'])


def test_resource_registration_is_private_unique_versioned_and_environment_bound(pg, monkeypatch):
    m, v = approved(pg)
    r = resource(pg, m, v)
    assert r['provider'] == 'BIGQUERY' and r['environment'] == 'development'
    assert 'physical_reference' not in r
    assert 'physical_reference' not in resources.read_resource(pg['workspace'], m, v, r['resource_id'], pg['owner'])
    with pytest.raises(psycopg.errors.UniqueViolation):
        resource(pg, m, v)
    with pytest.raises(psycopg.errors.UniqueViolation):
        resource(pg, m, v, generation=2)  # same physical table under another generation
    monkeypatch.setenv('APP_ENV', 'production')
    request = ResourceRegistration(environment='production', location='asia-south1', generation=1,
                                   physical_reference=dict(project='synthetic-project', dataset='production_dataset', table='generation_1'))
    production = resources.register_resource(pg['workspace'], m, v, pg['owner'], request)
    assert production['environment'] == 'production'
    with pytest.raises(HTTPException):
        runs.create_operation(pg['workspace'], m, v, pg['owner'], OperationCreate(
            operation_type='INCREMENTAL', resource_id=r['resource_id'], reason='Wrong environment'))
    with pytest.raises(ValueError):
        resource(pg, m, v, table='development_while_production', generation=3)
    with pytest.raises(psycopg.Error), pg['connect']() as conn:
        conn.execute("UPDATE warehouse_resources SET physical_reference=jsonb_set(physical_reference,'{table}','\"changed\"')")


@pytest.mark.parametrize('physical', [
    '{}', '{"reference_version":1,"project":"synthetic-project","dataset":"d","table":"t","secret":"bad"}',
    '{"reference_version":2,"project":"synthetic-project","dataset":"d","table":"t"}',
    '{"reference_version":1,"project":"synthetic-project","dataset":"d","table":null}'
])
def test_database_rejects_uncontrolled_physical_reference(pg, physical):
    m, v = approved(pg)
    with pytest.raises(psycopg.Error), pg['connect']() as conn:
        conn.execute('''INSERT INTO warehouse_resources(workspace_id,warehouse_model_id,warehouse_model_version_id,provider,
            environment,location,generation,physical_reference,created_by) VALUES (%s,%s,%s,'BIGQUERY','development','asia-south1',1,%s,%s)''',
            (pg['workspace'], m, v, physical, pg['owner']['user_id']))


def test_run_retry_terminal_history_and_no_execution(pg):
    _, _, _, op = operation(pg)
    w, u, oid = pg['workspace'], pg['owner'], op['operation_id']
    first = runs.new_attempt(w, oid, u)
    assert first['attempt_number'] == 1 and first['status'] == 'PENDING'
    with pytest.raises(psycopg.Error):
        runs.new_attempt(w, oid, u)
    with pytest.raises(psycopg.Error):
        runs.record_transition(w, oid, first['warehouse_run_id'], u, RunTransition(status='SUCCEEDED'))
    runs.record_transition(w, oid, first['warehouse_run_id'], u, RunTransition(status='RUNNING'))
    runs.record_transition(w, oid, first['warehouse_run_id'], u, RunTransition(status='FAILED', failure_code='SYNTHETIC_FAILURE'))
    saved = runs.read_run(w, oid, first['warehouse_run_id'], u)
    second = runs.new_attempt(w, oid, u)
    assert second['attempt_number'] == 2 and saved['failure_code'] == 'SYNTHETIC_FAILURE'
    runs.record_transition(w, oid, second['warehouse_run_id'], u, RunTransition(status='RUNNING'))
    runs.record_transition(w, oid, second['warehouse_run_id'], u, RunTransition(status='SUCCEEDED'))
    with pytest.raises(psycopg.Error):
        runs.record_transition(w, oid, first['warehouse_run_id'], u, RunTransition(status='RUNNING'))
    assert runs.read_run(w, oid, first['warehouse_run_id'], u) == saved
    with pytest.raises(psycopg.Error):
        runs.new_attempt(w, oid, u)
    for query in ('DELETE FROM warehouse_runs', 'UPDATE warehouse_operations SET reason=reason',
                  'UPDATE warehouse_runs SET created_by=created_by', 'TRUNCATE warehouse_runs'):
        with pytest.raises(psycopg.Error), pg['connect']() as conn:
            conn.execute(query)


def test_concurrent_retry_creates_one_pending_attempt(pg):
    _, _, _, op = operation(pg)
    def create():
        try:
            return runs.new_attempt(pg['workspace'], op['operation_id'], pg['owner'])['attempt_number']
        except psycopg.Error:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert results.count(1) == results.count('conflict') == 1


def test_migration_only_adds_objects_preserves_populated_tables_and_empty_rollback(pg):
    # Start from the fixture's populated application schema with no warehouse rows.
    with pg['connect']() as conn:
        existing = [row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename NOT LIKE 'warehouse_%%' ORDER BY tablename")]
        def snapshot():
            return {table: conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(table))).fetchall() for table in existing}
        def definitions():
            return conn.execute("""SELECT c.relname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull
                FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=current_schema() AND c.relname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""", (existing,)).fetchall()
        def catalog():
            constraints = conn.execute('''SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k
                JOIN pg_class c ON c.oid=k.conrelid WHERE c.relnamespace=current_schema()::regnamespace
                AND c.relname=ANY(%s) ORDER BY c.relname,k.conname''', (existing,)).fetchall()
            indexes = conn.execute('''SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema()
                AND tablename=ANY(%s) ORDER BY tablename,indexname''', (existing,)).fetchall()
            triggers = conn.execute('''SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=ANY(%s) AND NOT t.tgisinternal
                ORDER BY c.relname,t.tgname''', (existing,)).fetchall()
            functions = conn.execute("""SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p
                WHERE p.pronamespace=current_schema()::regnamespace AND p.proname NOT LIKE 'warehouse_%%' ORDER BY p.proname""").fetchall()
            return constraints, indexes, triggers, functions
        before, columns, old_catalog = snapshot(), definitions(), catalog()
        for _ in range(2):
            conn.execute(ROLLBACK.read_text(encoding='utf-8'))
            conn.execute(MIGRATION.read_text(encoding='utf-8'))
            assert snapshot() == before and definitions() == columns and catalog() == old_catalog
        names = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE 'warehouse_%%'").fetchall()
        assert {row[0] for row in names} == set(TABLES)
        invalid = conn.execute("SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid WHERE c.relnamespace=current_schema()::regnamespace AND (NOT indisvalid OR NOT indisready)").fetchone()[0]
        assert invalid == 0
    draft(pg)
    with pytest.raises(psycopg.Error), pg['connect']() as conn:
        conn.execute(ROLLBACK.read_text(encoding='utf-8'))
    assert len(models.list_models(pg['workspace'], pg['owner'])) == 1
