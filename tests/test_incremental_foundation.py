"""Actual PostgreSQL foundation tests. State fixtures are metadata, never a merge engine."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import psycopg
import pytest
from fastapi.testclient import TestClient
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline, approve_first, execute
from app.schemas.incremental import LoadPolicyRequest
from app.services.incremental_policy_service import save_policy, read_foundation, IncrementalConflict
from app.services import incremental_application_service as applications
from app.services.dataset_processing_service import dataset_lock
from app.services.processing_service import start_processing
from app.services.delivery_lifecycle_service import archive_delivery, DeliveryArchiveConflict
from app.api.identity import get_current_user
from app.main import app


def policy(pipeline, **changes):
    db = pipeline[0]
    fields = dict(dataset_version_id=pipeline[3]['dataset_version']['dataset_version_id'],expected_policy_version=0,
                  load_strategy='UPSERT',business_keys=['id','status'],schema_evolution_policy='STRICT')
    return save_policy(db[2],db[3],db[1],LoadPolicyRequest(**(fields | changes)))


def prepare(pipeline, policy_id, upload_id=None):
    db=pipeline[0]
    return applications.prepare_application(db[2],db[3],db[1],upload_id or pipeline[2],policy_id)


def second_delivery(pipeline):
    db=pipeline[0]
    with db[0]() as conn:
        return conn.execute("INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING upload_id",
                            (db[1]['user_id'],db[2],db[3],pipeline[3]['file'][0])).fetchone()[0]


def ready(pipeline):
    approve_first(pipeline); execute(pipeline)
    p=policy(pipeline)
    return p,prepare(pipeline,p['policy_id'])


def candidate(pipeline, application, previous=None):
    """Synthetic metadata fixture only; deliberately no object store/row application."""
    db=pipeline[0]
    with db[0]() as conn:
        conn.execute("UPDATE delivery_applications SET status='RUNNING',started_at=NOW() WHERE application_id=%s",(application['application_id'],))
        return conn.execute("""INSERT INTO dataset_state_versions(dataset_id,dataset_version_id,policy_id,source_application_id,previous_state_id,
            state_version,manifest_key,manifest_sha256,row_count,status,validated_at) VALUES (%s,%s,%s,%s,%s,
            (SELECT COALESCE(MAX(state_version),0)+1 FROM dataset_state_versions WHERE dataset_id=%s),%s,%s,2,'VALIDATED',NOW()) RETURNING state_id""",
            (db[3],application['dataset_version_id'],application['policy_id'],application['application_id'],previous,db[3],
             f"silver/dataset_id={db[3]}/dataset_version_id={application['dataset_version_id']}/state/application_id={application['application_id']}/candidate-{__import__('uuid').uuid4().hex}/manifest.json",'a'*64)).fetchone()[0]


def publish(pipeline, application):
    db=pipeline[0]
    return applications.publish_validated_state(db[2],db[3],db[1],application['application_id'])


def test_physical_reuse_distinct_delivery_applications_pins_and_null_counts(pipeline):
    p,first=ready(pipeline)
    second=prepare(pipeline,p['policy_id'],second_delivery(pipeline))
    assert first['application_id'] != second['application_id']
    assert first['upload_request_id'] != second['upload_request_id']
    assert first['dataset_version_file_id'] == second['dataset_version_file_id']
    assert first['source_dq_run_id'] == second['source_dq_run_id']
    assert first['applied_rule_version'] == second['applied_rule_version'] == 1
    assert (first['input_rows'],first['valid_rows'],first['rejected_rows']) == (3,2,1)
    for key in ['inserted_rows','updated_rows','unchanged_rows','duplicate_rows','deactivated_rows','current_state_rows']:
        assert first[key] is None
    assert prepare(pipeline,p['policy_id']) == first
    with pipeline[0][0]() as conn:
        with pytest.raises(psycopg.IntegrityError):
            conn.execute("""INSERT INTO delivery_applications(upload_request_id,dataset_id,dataset_version_id,dataset_version_file_id,policy_id,
                applied_rule_version,source_dq_run_id,ingestion_time) SELECT upload_request_id,dataset_id,dataset_version_id,dataset_version_file_id,
                policy_id,applied_rule_version,source_dq_run_id,ingestion_time FROM delivery_applications WHERE application_id=%s""",(first['application_id'],))


def test_policy_validation_versions_and_immutable_pins(pipeline):
    p,first=ready(pipeline)
    assert policy(pipeline)['policy_id'] == p['policy_id']
    with pytest.raises(ValueError): policy(pipeline,business_keys=['missing'])
    with pytest.raises(ValueError): policy(pipeline,event_time_column='status')
    with pytest.raises(ValueError): policy(pipeline,dataset_version_id=pipeline[0][5])
    with pytest.raises(IncrementalConflict): policy(pipeline,expected_policy_version=1,load_strategy='APPEND')
    newer=policy(pipeline,expected_policy_version=1,load_strategy='APPEND',confirm_policy_change=True)
    assert newer['policy_version']==2
    assert prepare(pipeline,p['policy_id'])==first
    with pytest.raises(IncrementalConflict): prepare(pipeline,newer['policy_id'])
    with pytest.raises(IncrementalConflict): policy(pipeline,load_strategy='SNAPSHOT',confirm_policy_change=True)
    with pipeline[0][0]() as conn:
        with pytest.raises(psycopg.DatabaseError): conn.execute("UPDATE dataset_load_policies SET load_strategy='APPEND' WHERE policy_id=%s",(p['policy_id'],))


def test_unapproved_or_archived_delivery_cannot_prepare(pipeline):
    p=policy(pipeline)
    with pytest.raises(ValueError): prepare(pipeline,p['policy_id'])
    approve_first(pipeline)
    from app.services.processing_service import run_silver_processing
    run_silver_processing(pipeline[3]['dataset_version_file'][0],'test')
    db=pipeline[0]
    archive_delivery(pipeline[2],db[1],db[2],db[3])
    with pytest.raises(DeliveryArchiveConflict): prepare(pipeline,p['policy_id'])
    assert read_foundation(db[2],db[3],db[1])['applications']==[]


def test_schema_change_cannot_prepare_using_old_policy(pipeline):
    p,_=ready(pipeline)
    changed,_=pipeline[1]('id,name\ntext,Alice\n')
    with pytest.raises(ValueError,match='schema'): prepare(pipeline,p['policy_id'],changed)


def test_ownership_and_api_no_private_paths(pipeline):
    p,first=ready(pipeline); db=pipeline[0]
    app.dependency_overrides[get_current_user]=lambda:db[1]
    try:
        client=TestClient(app); base=f'/workspaces/{db[2]}/datasets/{db[3]}/incremental'
        response=client.get(base)
        assert response.status_code==200 and response.json()['execution_available'] is True
        assert response.json()['executable_modes']==['APPEND','UPSERT']
        assert response.json()['current_state'] is None
        assert not any(secret in response.text for secret in ['manifest_key','silver_path','s3://'])
        assert client.post(base+'/applications/prepare',json={'upload_request_id':pipeline[2],'policy_id':p['policy_id']}).json()['application_id']==first['application_id']
        assert client.get(f'/workspaces/{db[2]+999}/datasets/{db[3]}/incremental').status_code==403
        assert client.post(base+'/applications/prepare',json={'upload_request_id':pipeline[2],'policy_id':p['policy_id']+999}).status_code==400
        app.dependency_overrides[get_current_user]=lambda:{'user_id':db[1]['user_id']+999,'owner_key':'dev:other'}
        assert client.get(base).status_code==403
        assert client.post(base+'/policies',json={'dataset_version_id':first['dataset_version_id'],'expected_policy_version':0,'load_strategy':'APPEND','schema_evolution_policy':'STRICT'}).status_code==403
    finally: app.dependency_overrides.clear()


def test_dataset_concurrency_guard(pipeline):
    db=pipeline[0]; entered,release=Event(),Event()
    def hold():
        with dataset_lock(db[2],db[3]):
            entered.set(); assert release.wait(15)
    with ThreadPoolExecutor(2) as pool:
        task=pool.submit(hold)
        assert entered.wait(10)
        try:
            with pytest.raises(RuntimeError,match='already active'): policy(pipeline)
        finally: release.set(); task.result()


def test_atomic_publication_rollback_idempotency_history_archive(pipeline,monkeypatch):
    p,first=ready(pipeline); db=pipeline[0]; state_id=candidate(pipeline,first)
    original=applications.public_application
    def fail(_): raise RuntimeError('injected precommit failure')
    monkeypatch.setattr(applications,'public_application',fail)
    with pytest.raises(RuntimeError): publish(pipeline,first)
    with db[0]() as conn:
        assert conn.execute('SELECT current_state_id FROM datasets WHERE dataset_id=%s',(db[3],)).fetchone()[0] is None
        assert conn.execute('SELECT status FROM dataset_state_versions WHERE state_id=%s',(state_id,)).fetchone()[0]=='VALIDATED'
    monkeypatch.setattr(applications,'public_application',original)
    result=publish(pipeline,first)
    assert result['result_state_id']==state_id and result['status']=='SUCCESS'
    assert publish(pipeline,first)==result
    for table,column,value,identifier in [('delivery_applications','status',"'FAILED'",'application_id'),('dataset_state_versions','row_count','99','state_id')]:
        with db[0]() as conn:
            with pytest.raises(psycopg.DatabaseError): conn.execute(f'UPDATE {table} SET {column}={value} WHERE {identifier}=%s',(first['application_id'] if identifier=='application_id' else state_id,))
    with pytest.raises(IncrementalConflict): policy(pipeline,expected_policy_version=1,load_strategy='APPEND',confirm_policy_change=True)
    archive_delivery(pipeline[2],db[1],db[2],db[3])
    read=read_foundation(db[2],db[3],db[1])
    assert read['current_state']['state_id']==state_id and read['applications'][0]['archived_at']
    assert publish(pipeline,first)==result


def test_stale_state_candidate_does_not_change_head(pipeline):
    p,first=ready(pipeline)
    second=prepare(pipeline,p['policy_id'],second_delivery(pipeline))
    first_state=candidate(pipeline,first); candidate(pipeline,second)
    publish(pipeline,first)
    with pytest.raises(IncrementalConflict): publish(pipeline,second)
    assert read_foundation(pipeline[0][2],pipeline[0][3],pipeline[0][1])['current_state']['state_id']==first_state
    # Recovery reserves a new immutable candidate; it never overwrites the stale one.
    next_state=candidate(pipeline,second,previous=first_state)
    assert publish(pipeline,second)['result_state_id']==next_state
    with pipeline[0][0]() as conn:
        with pytest.raises(psycopg.DatabaseError): conn.execute('UPDATE datasets SET current_state_id=%s WHERE dataset_id=%s',(first_state,pipeline[0][3]))


def test_other_owned_dataset_cannot_share_policy_or_application(pipeline):
    p,first=ready(pipeline); db=pipeline[0]
    with db[0]() as conn:
        other=conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES (%s,'Other entity','dev:test') RETURNING dataset_id",(db[2],)).fetchone()[0]
        upload=conn.execute("INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING upload_id",
                            (db[1]['user_id'],db[2],other,pipeline[3]['file'][0])).fetchone()[0]
    start_processing(upload)
    assert read_foundation(db[2],other,db[1])['applications']==[]
    with pytest.raises(PermissionError): prepare(pipeline,p['policy_id'],upload)
    with pytest.raises(ValueError): applications.prepare_application(db[2],other,db[1],upload,p['policy_id'])
    with pytest.raises(ValueError): save_policy(db[2],other,db[1],LoadPolicyRequest(dataset_version_id=first['dataset_version_id'],expected_policy_version=0,
        load_strategy='APPEND',schema_evolution_policy='STRICT'))


def test_api_failure_details_are_sanitized(pipeline,monkeypatch):
    import app.api.incremental as api
    db=pipeline[0]; app.dependency_overrides[get_current_user]=lambda:db[1]
    def fail(*args): raise RuntimeError('private s3://bucket/key and row contents')
    monkeypatch.setattr(api,'read_foundation',fail)
    try:
        r=TestClient(app).get(f'/workspaces/{db[2]}/datasets/{db[3]}/incremental')
        assert r.status_code==409 and 'private' not in r.text and 's3://' not in r.text
    finally: app.dependency_overrides.clear()
