"""Real isolated PostgreSQL adoption; never operates on the development public schema."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import psycopg
import pytest
pytest.importorskip('tests.test_rule_approval_postgres', reason='Historical PostgreSQL fixtures require tools/ownership_compatibility_regression.py')
from fastapi import HTTPException
from app.db import ownership_adoption_repository as repository
from app.db import sql_repository
from app.db.ownership_identity import source_membership
from app.services.ownership_adoption_plan import prepare_manifest
from app.services.ownership_adoption_service import adoption
from app.services import ownership_adoption_service as executor
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline
from tests.test_append_execution import append_pipeline
from tests.test_semantic_profiles import profiled
from tests.test_dataset_understanding import ready
from tests.test_workspace_understanding import workspace_ready, invoke
from tests.test_relationships import relationship_ready

ROOT = Path(executor.__file__).resolve().parents[2]


def install(db):
    with db[0]() as conn:
        assert conn.execute('SELECT current_schema()').fetchone()[0].startswith('test_')
        if conn.execute("SELECT to_regclass('ownership_adoption_events')").fetchone()[0] is None:
            conn.execute((ROOT / 'migrations/pending/0028_ownership_adoption_events.sql').read_text())
        conn.execute("UPDATE users SET email_verified=true,account_status='ACTIVE' WHERE user_id=%s", (db[1]['user_id'],))
        target = conn.execute("INSERT INTO users(owner_key,display_name,email_verified,account_status) VALUES ('usr:adoption-target','Synthetic target',true,'ACTIVE') RETURNING user_id").fetchone()[0]
        third = conn.execute("INSERT INTO users(owner_key,display_name,email_verified,account_status) VALUES ('usr:unrelated','Synthetic third user',true,'ACTIVE') RETURNING user_id").fetchone()[0]
    with db[0]() as conn:
        manifest = prepare_manifest(repository.snapshot(conn, db[1]['owner_key'], target), db[1]['owner_key'], target)
    return manifest, {'user_id': target, 'owner_key': 'usr:adoption-target'}, {'user_id': third, 'owner_key': 'usr:unrelated'}


def run(db, manifest, **kwargs):
    return adoption(manifest, manifest['target_user_id'], connection_factory=db[0], **kwargs)


def test_actual_transaction_replay_inverse_and_append_only(pg):
    m, target, third = install(pg)
    assert run(pg, m)['status'] == 'READY_TO_ADOPT'
    assert run(pg, m, execute=True)['status'] == 'ADOPTED'
    replay = run(pg, m, execute=True)
    assert replay['status'] == 'ALREADY_ADOPTED' and replay['updates'] == replay['provenance_inserts'] == []
    with pg[0]() as conn:
        assert conn.execute('SELECT count(*) FROM ownership_adoption_events').fetchone()[0] == 1
        assert repository.snapshot(conn, m['source_principal'], target['user_id'])['invariants'] == m['invariants']
    for statement in ('UPDATE ownership_adoption_events SET status=status', 'DELETE FROM ownership_adoption_events', 'TRUNCATE ownership_adoption_events'):
        with pytest.raises(psycopg.Error), pg[0]() as conn:
            conn.execute(statement)
    assert run(pg, m, execute=True, operation='REVERSE')['status'] == 'REVERSED'
    assert run(pg, m, execute=True, operation='REVERSE')['status'] == 'ALREADY_REVERSED'
    with pg[0]() as conn:
        assert conn.execute('SELECT owner FROM datasets').fetchone()[0] == m['source_principal']
        assert conn.execute('SELECT count(*) FROM ownership_adoption_events').fetchone()[0] == 2


def test_failure_after_partial_owner_write_rolls_everything_back(pg, monkeypatch):
    m, _, _ = install(pg)
    original = repository.snapshot
    calls = []
    def fail_after_write(*args):
        calls.append(True)
        if len(calls) == 2:
            raise ValueError('Synthetic post-update verification failure')
        return original(*args)
    monkeypatch.setattr(repository, 'snapshot', fail_after_write)
    assert run(pg, m, execute=True)['status'] == 'FAILED'
    with pg[0]() as conn:
        assert conn.execute('SELECT owner FROM workspaces').fetchone()[0] == m['source_principal']
        assert conn.execute('SELECT owner FROM datasets').fetchone()[0] == m['source_principal']
        assert conn.execute('SELECT count(*) FROM ownership_adoption_events').fetchone()[0] == 0


def test_concurrent_invocations_commit_one_event(pg):
    m, _, _ = install(pg)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(pg, m, execute=True)['status'], range(2)))
    assert sorted(results) == ['ADOPTED', 'ALREADY_ADOPTED']


def test_scope_and_fingerprint_changes_refuse_execution(pg):
    m, _, _ = install(pg)
    with pg[0]() as conn:
        conn.execute("UPDATE workspaces SET description='Intervening edit'")
    assert run(pg, m, execute=True)['status'] == 'SCOPE_CHANGED'
    with pg[0]() as conn:
        assert conn.execute('SELECT count(*) FROM ownership_adoption_events').fetchone()[0] == 0


def test_sql_history_uses_current_owner_preserves_original_actor(pg):
    m, target, third = install(pg)
    from app.schemas.sql_analytics import SaveQuery
    request = SaveQuery(name='Original', sql_text='SELECT 1')
    saved = sql_repository.save(pg[2], pg[1], request)
    sql_repository.record(pg[2], pg[1], 'SELECT 1', 'a'*64, {'status': 'SUCCEEDED'}, saved['query_id'])
    with pg[0]() as conn:
        m = prepare_manifest(repository.snapshot(conn, m['source_principal'], target['user_id']), m['source_principal'], target['user_id'])
    assert run(pg, m, execute=True)['status'] == 'ADOPTED'
    assert sql_repository.queries(pg[2], target)[0]['owner_key'] == pg[1]['owner_key']
    assert sql_repository.history(pg[2], target)[0]['owner_key'] == pg[1]['owner_key']
    changed = SaveQuery(name='Current-owner edit', sql_text='SELECT 2', expected_version=1)
    assert sql_repository.save(pg[2], target, changed, saved['query_id'])['owner_key'] == pg[1]['owner_key']
    sql_repository.record(pg[2], target, 'SELECT 2', 'b'*64, {'status': 'SUCCEEDED'}, saved['query_id'])
    assert sql_repository.history(pg[2], target)[0]['owner_key'] == target['owner_key']
    for other in (pg[1], third):
        for read in (sql_repository.queries, sql_repository.history):
            with pytest.raises(HTTPException): read(pg[2], other)
        with pg[0]() as conn, pytest.raises(HTTPException):
            sql_repository.owned_query(conn, pg[2], other, saved['query_id'])


def test_profiles_semantics_signatures_and_genuine_drift(workspace_ready):
    from app.services import dataset_profile_service as profiles, dataset_understanding_service as semantic
    from app.services import workspace_understanding_service as workspace, relationship_service as relationships
    from app.services import metric_service as metrics, sql_catalog_service as catalog
    from app.db.database import repository_transaction
    f, _ = workspace_ready
    db = f[0][0]
    before_profile = profiles.read_profile(db[2], db[3], db[1])
    before_semantic = semantic.read_understanding(db[2], db[3], db[1])
    before_ws = invoke(f, workspace.generate_workspace)
    with repository_transaction() as conn:
        before_rel = relationships.gather(conn, db[2], db[1])['signature']
        before_metric = metrics.gather(conn, db[2], db[1])['signature']
        before_catalog = catalog.gather(conn, db[2], db[1])
    m, target, _ = install(db)
    assert run(db, m, execute=True)['status'] == 'ADOPTED'
    assert profiles.read_profile(db[2], db[3], target) == before_profile
    assert profiles.refresh_profile(db[2], db[3], target) == before_profile
    assert semantic.read_understanding(db[2], db[3], target) == before_semantic
    assert workspace.read_understanding_workspace(db[2], target) == before_ws
    with repository_transaction() as conn:
        assert relationships.gather(conn, db[2], target)['signature'] == before_rel
        assert metrics.gather(conn, db[2], target)['signature'] == before_metric
        after_catalog = catalog.gather(conn, db[2], target)
        ids = [d['dataset_id'] for d in before_catalog['datasets']]
        assert catalog.signature(before_catalog,ids,before_catalog['relationships']) == catalog.signature(after_catalog,ids,after_catalog['relationships'])
    # A new model config can generate against old-owner profile evidence.
    f[2].setattr(semantic.get_provider(), 'model', 'after-adoption')
    assert semantic.generate_understanding(db[2], db[3], target)['status'] == 'READY'
    with db[0]() as conn:
        conn.execute("UPDATE datasets SET dataset_name='Real source change' WHERE dataset_id=%s", (db[3],))
    assert workspace.read_understanding_workspace(db[2], target)['status'] == 'STALE'
    with pytest.raises(PermissionError): profiles.read_profile(db[2], db[3], db[1])


def test_synthetic_cross_user_http_isolation_and_historical_uploads(pg):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api.identity import get_current_user
    from app.db.workspace_repository import list_workspaces_by_owner
    from app.db.processing_repository import list_dataset_deliveries
    from app.services.processing_context_service import owned_upload
    m, target, third = install(pg)
    with pg[0]() as conn:
        upload = conn.execute("INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING upload_id", (pg[1]['user_id'],pg[2],pg[3],pg[4])).fetchone()[0]
        m = prepare_manifest(repository.snapshot(conn,m['source_principal'],target['user_id']),m['source_principal'],target['user_id'])
    def deny(user):
        assert list_workspaces_by_owner(user['owner_key']) == []
        app.dependency_overrides[get_current_user] = lambda: user
        ws = f'/workspaces/{pg[2]}'
        ds = f'{ws}/datasets/{pg[3]}'
        paths = [ws, ws+'/datasets', ds, ds+'/processing', ds+'/profile', ds+'/profile/readiness',
                 ds+'/semantic-understanding', ws+'/semantic-understanding', ws+'/relationships', ws+'/metrics',
                 ws+'/analytics', ws+'/sql/catalog', ws+'/sql/queries', ws+'/sql/executions',
                 f'/files/uploads/{upload}/processing-context']
        with TestClient(app) as client:
            for path in paths:
                result = client.get(path)
                assert result.status_code in (403,404), (path,result.status_code,result.text)
            for path, body in [(ws+'/sql/execute', {'sql_text':'SELECT 1'}),
                               (ws+'/questions', {'question':'Count orders'})]:
                result = client.post(path,json=body)
                assert result.status_code in (403,404), (path,result.status_code,result.text)
        with pytest.raises((PermissionError,ValueError)): owned_upload(upload,user)
        assert list_dataset_deliveries(user['user_id'],pg[2],pg[3]) == []
    try:
        deny(third)
        assert run(pg,m,execute=True)['status'] == 'ADOPTED'
        assert owned_upload(upload,target)[1] == pg[1]['user_id']
        assert len(list_dataset_deliveries(target['user_id'],pg[2],pg[3])) == 1
        deny(pg[1]); deny(third)
    finally:
        app.dependency_overrides.pop(get_current_user,None)


def test_migration_is_guarded_and_scope_validation_is_strict(pg):
    install(pg)
    with pg[0]() as conn:
        assert not conn.execute("SELECT adoption_scope_valid('{\"1\":[1,1]}'::jsonb,1,2)").fetchone()[0]
        assert not conn.execute("SELECT adoption_scope_valid('{\"1\":[1.1]}'::jsonb,1,1)").fetchone()[0]
    with pytest.raises(psycopg.Error), pg[0]() as conn:
        conn.execute((ROOT/'migrations/pending/0028_ownership_adoption_events.sql').read_text())


def test_schema_rollback_empty_only_and_round_trip(pg):
    m, target, _ = install(pg)
    rollback=(ROOT/'migrations/pending/0028_ownership_adoption_events.rollback.sql').read_text()
    with pg[0]() as conn:
        before=repository.snapshot(conn,m['source_principal'],target['user_id'])
        conn.execute(rollback)
        assert conn.execute("SELECT to_regclass('ownership_adoption_events')").fetchone()[0] is None
        conn.execute((ROOT/'migrations/pending/0028_ownership_adoption_events.sql').read_text())
        assert repository.snapshot(conn,m['source_principal'],target['user_id'])==before
    assert run(pg,m,execute=True)['status']=='ADOPTED'
    with pytest.raises(psycopg.Error), pg[0]() as conn:
        conn.execute(rollback)


def test_failed_workspace_and_metric_headers_retry_after_adoption(workspace_ready):
    from psycopg.types.json import Jsonb
    from app.services import workspace_understanding_service as workspace, metric_service as metrics
    from app.ai.semantic_provider import ProviderFailure
    from app.db.database import repository_transaction
    f,provider=workspace_ready; db=f[0][0]
    first=invoke(f,workspace.generate_workspace)
    with repository_transaction() as conn:
        source=metrics.gather(conn,db[2],db[1])
        run_id=conn.execute('''INSERT INTO metric_discovery_runs(workspace_id,owner_key,run_version,algorithm_version,
            policy_version,source_signature,configuration_key,source_pins,workspace_suggestion_id,provider,model,status)
            VALUES (%s,%s,1,1,1,%s,'synthetic-retry',%s,%s,'synthetic','synthetic','FAILED') RETURNING run_id''',
            (db[2],db[1]['owner_key'],source['signature'],Jsonb(source['source_pins']),first['suggestion']['suggestion_id'])).fetchone()[0]
    provider.model='retry-context'; provider.failure=ProviderFailure('TIMEOUT')
    failed=invoke(f,workspace.generate_workspace)
    assert failed['status']=='FAILED'
    m,target,_=install(db)
    assert run(db,m,execute=True)['status']=='ADOPTED'
    provider.failure=None
    retried=workspace.generate_workspace(db[2],target)
    assert retried['status']=='READY'
    with db[0]() as conn:
        assert conn.execute('SELECT owner_key FROM workspace_semantic_suggestions WHERE suggestion_id=%s',
                            (retried['suggestion']['suggestion_id'],)).fetchone()[0]==db[1]['owner_key']
        conn.execute("UPDATE metric_discovery_runs SET status='GENERATING' WHERE run_id=%s",(run_id,))
        conn.execute("UPDATE metric_discovery_runs SET status='READY',completed_at=NOW(),failure_code=NULL WHERE run_id=%s",(run_id,))
        assert conn.execute('SELECT owner_key FROM metric_discovery_runs WHERE run_id=%s',(run_id,)).fetchone()[0]==db[1]['owner_key']
        assert not conn.execute('SELECT adoption_history_actor_matches(%s,%s,%s,%s)',
                                (db[2],999999,db[1]['owner_key'],target['owner_key'])).fetchone()[0]


def test_failed_relationship_header_retries_after_adoption(relationship_ready):
    from psycopg.types.json import Jsonb
    from app.services import relationship_service as relationships
    from app.db.database import repository_transaction
    f,_=relationship_ready; db=f[0][0]
    with repository_transaction() as conn:
        source=relationships.gather(conn,db[2],db[1])
        run_id=conn.execute('''INSERT INTO relationship_discovery_runs(workspace_id,owner_key,run_version,algorithm_version,
            source_signature,source_pins,coverage,semantic_context,status)
            VALUES (%s,%s,1,%s,%s,%s,%s,%s,'FAILED') RETURNING run_id''',
            (db[2],db[1]['owner_key'],relationships.ALGORITHM_VERSION,source['signature'],
             Jsonb(source['pins']),Jsonb(source['coverage']),Jsonb(source['semantic_context']))).fetchone()[0]
    m,target,_=install(db)
    assert run(db,m,execute=True)['status']=='ADOPTED'
    result=relationships.discover(db[2],target)
    assert result['status']=='READY'
    with db[0]() as conn:
        assert conn.execute('SELECT owner_key FROM relationship_discovery_runs WHERE run_id=%s',(run_id,)).fetchone()[0]==db[1]['owner_key']
