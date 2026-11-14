"""Real PostgreSQL pipeline and restricted metadata command. Local disposable DB only.

Set ISSUE1_TEST_DATABASE_URL to the dedicated loopback test database.
Each test owns a fresh database; deployed environments are explicitly rejected.
"""
import os
import re
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
import pytest
import psycopg
from psycopg import sql
from app.db import database
from test_task20_processing_postgres import pipeline, approve
from test_processing_jobs_postgres import source


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv('ISSUE1_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Dedicated disposable Issue #1 database required')
    parsed = urlsplit(dsn)
    if parsed.hostname != '127.0.0.1' or parsed.port != 55442 or parsed.path != '/issue1':
        pytest.fail('Issue #1 tests refuse non-disposable targets')
    name = 'issue1_' + uuid4().hex
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    schema = 'test_phase5a_' + uuid4().hex
    original_connect = psycopg.connect
    def connect(*args, **kwargs):
        return original_connect(dsn, dbname=name, connect_timeout=5, options=f'-c search_path={schema} -c statement_timeout=15000 -c lock_timeout=3000')
    try:
        with connect() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO datarise_api').format(sql.Identifier(schema)))
            for path in sorted(Path('migrations').glob('*.sql')):
                # Processing baseline and cumulative/profile/SQL upgrades; no warehouse execution migrations.
                if int(path.name[:4]) > 45 and 'warehouse' in path.name:
                    continue
                statement = path.read_text(encoding='utf-8-sig').replace('public.', schema + '.')
                statement = re.sub(r'pg_catalog,\s*public,\s*pg_temp', 'pg_catalog,' + schema + ',pg_temp', statement)
                conn.execute(statement)
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO datarise_authority_owner').format(sql.Identifier(schema)))
            conn.execute('GRANT SELECT ON users,workspaces,datasets,processing_jobs,upload_requests TO datarise_authority_owner')
            command = Path('migrations/pending/0068_processing_job_metadata.sql').read_text()
            conn.execute(command.replace('public.', schema + '.').replace('pg_catalog,public,pg_temp', 'pg_catalog,' + schema + ',pg_temp'))
            user = conn.execute("INSERT INTO users(owner_key,display_name) VALUES ('dev:test','Test') RETURNING user_id").fetchone()[0]
            ws = conn.execute("INSERT INTO workspaces(workspace_name,owner) VALUES ('Test','dev:test') RETURNING workspace_id").fetchone()[0]
            ds = conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES (%s,'Orders','dev:test') RETURNING dataset_id", (ws,)).fetchone()[0]
            file = conn.execute("INSERT INTO physical_files(file_name,file_size,file_hash,storage_path,status,schema_hash) VALUES ('a.csv',10,%s,'test-only','UPLOADED',%s) RETURNING file_id", ('a'*64, 'b'*64)).fetchone()[0]
            version = conn.execute("INSERT INTO dataset_versions(dataset_id,version_number,schema_hash) VALUES (%s,1,%s) RETURNING dataset_version_id", (ds, 'b'*64)).fetchone()[0]
            association = conn.execute("INSERT INTO dataset_version_files(dataset_version_id,file_id,status) VALUES (%s,%s,'AWAITING_RULES') RETURNING dataset_version_file_id", (version,file)).fetchone()[0]
        monkeypatch.setattr(database, '_api_database_connection', connect)
        from app.services import business_rule_service as rules, rule_reuse_service as reuse
        monkeypatch.setattr(rules, 'generate_rule_suggestions', lambda _: [dict(column_name='id',suggested_rule_type='NOT_NULL',options=['YES','NO'])])
        monkeypatch.setattr(reuse, 'generate_rule_suggestions', rules.generate_rule_suggestions)
        yield connect, dict(user_id=user,owner_key='dev:test'), ws, ds, file, version, association
    finally:
        with original_connect(dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


def test_real_stages_idempotency_and_metadata_boundary(pipeline):
    src, client, run = pipeline
    connect, actor, upload = src
    bronze = run('BRONZE')
    approve(src)
    silver = run('SILVER')
    gold = run('GOLD')
    again = run('GOLD')
    assert again['job_id'] == gold['job_id']
    with connect() as conn:
        ws,ds = conn.execute('SELECT workspace_id,dataset_id FROM upload_requests WHERE upload_id=%s',(upload,)).fetchone()
        # Reuse the restricted production role; the function exposes only five fields.
        conn.execute('SET LOCAL ROLE datarise_api')
        rows = conn.execute('SELECT * FROM task24_processing_jobs(%s,%s,%s)',(actor,ws,ds)).fetchall()
        assert len(rows) == 3
        assert all(len(row)==5 and row[3]=='SUCCEEDED' for row in rows)
        assert {row[2] for row in rows} == {'BRONZE','SILVER','GOLD'}
        assert not conn.execute("SELECT has_table_privilege(current_user,'processing_jobs','SELECT')").fetchone()[0]
    with connect() as conn:
        assert conn.execute('SELECT total_rows,valid_rows,rejected_rows FROM data_quality_runs').fetchone() == (2,2,0)
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0] == 3
        other = conn.execute("INSERT INTO users(owner_key,display_name) VALUES ('dev:stranger','Other') RETURNING user_id").fetchone()[0]
    with connect() as conn:
        conn.execute('SET LOCAL ROLE datarise_api')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('SELECT * FROM task24_processing_jobs(%s,%s,%s)',(other,ws,ds))
    with connect() as conn:
        conn.execute('SET LOCAL ROLE datarise_api')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('SELECT lease_token FROM processing_jobs')
    assert all(key.startswith(('raw/','artifact-candidates/')) for key in client.latest)


def test_first_append_publication_profile_model_and_sql(pipeline, monkeypatch):
    from app.services import dataset_analytics_service as analytics, dataset_profile_service as profiles
    from app.services import sql_catalog_service, sql_analytics_service
    from app.api.workspace_model import model_summary
    from app.storage.cumulative_gold_artifacts import CumulativeGoldArtifacts
    from app.storage.incremental_artifacts import IncrementalArtifacts
    from app.processing.metric_execution import BoundedMetricArtifacts
    from app.schemas.sql_analytics import SqlRequest

    src, client, run = pipeline
    connect, actor, upload = src
    user = dict(user_id=actor,owner_key='dev:test')
    class LatestVersionClient:
        # Match S3's ordinary GET semantics for cumulative readers while the
        # worker continues to use explicit immutable versions.
        def get_object(self, **kwargs):
            kwargs.setdefault('VersionId', client.latest[kwargs['Key']])
            return client.get_object(**kwargs)
        def put_object(self, **kwargs):
            return client.put_object(**kwargs)
    latest = LatestVersionClient()
    run('BRONZE')
    approve(src)
    run('SILVER')
    with connect() as conn:
        ws,ds = conn.execute('SELECT workspace_id,dataset_id FROM upload_requests WHERE upload_id=%s',(upload,)).fetchone()
        policy = conn.execute('SELECT task20_initial_append_policy(%s,%s)',(actor,upload)).fetchone()[0]
        assert conn.execute('SELECT task20_initial_append_policy(%s,%s)',(actor,upload)).fetchone()[0] == policy
    # The existing worker reuses verified Silver and applies the first APPEND.
    run('SILVER','approved-policy-apply')
    run('GOLD')
    with connect() as conn:
        assert conn.execute('SELECT row_count FROM dataset_state_versions WHERE state_id=(SELECT current_state_id FROM datasets WHERE dataset_id=%s)',(ds,)).fetchone()[0] == 2
        assert conn.execute('SELECT status FROM delivery_applications WHERE upload_request_id=%s',(upload,)).fetchone()[0] == 'SUCCESS'
    monkeypatch.setattr(analytics,'CumulativeGoldArtifacts',lambda:CumulativeGoldArtifacts(latest))
    monkeypatch.setattr(profiles,'IncrementalArtifacts',lambda:IncrementalArtifacts(latest))
    monkeypatch.setattr(BoundedMetricArtifacts,'create',lambda:CumulativeGoldArtifacts(latest))
    analytics.refresh_analytics(ws,ds,user)
    assert analytics.readiness(ws,ds,user)['analytics_ready']
    profiles.refresh_profile(ws,ds,user)
    assert profiles.read_profile(ws,ds,user)['profile_ready']
    model = model_summary(ws,user)
    assert any(d['dataset']['id']==ds and d['profile']['summary']['row_count']==2 for d in model['datasets'])
    catalog = sql_catalog_service.snapshot(ws,user)
    alias = next(d['alias'] for d in catalog['datasets'] if d['dataset_id']==ds)
    result = sql_analytics_service.execute_query(ws,user,SqlRequest(sql_text=f'SELECT COUNT(*) AS n FROM "{alias}"'))
    assert result['status']=='SUCCEEDED', result['diagnostics']
    assert result['rows']==[[2]]
    assert sql_analytics_service.history(ws,user)[0]['execution_id']==result['execution_id']
