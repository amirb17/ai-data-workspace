"""Logical archive with real PostgreSQL lineage and no physical cleanup."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline, approve_first, execute, counts
from app.services.delivery_lifecycle_service import archive_delivery, lifecycle_lock, DeliveryArchiveConflict
from app.services import processing_context_service as contexts, dataset_processing_service as datasets
from app.services import processing_service as stages
from app.main import app
from app.api.identity import get_current_user


def archive(db, upload):
    return archive_delivery(upload, db[1], db[2], db[3])


def test_unprocessed_repeat_scope_refresh_and_pending(pg):
    with pg[0]() as conn:
        conn.execute("UPDATE physical_files SET schema_hash=NULL WHERE file_id=%s", (pg[4],))
        conn.execute("DELETE FROM dataset_version_files WHERE dataset_version_file_id=%s", (pg[6],))
        upload = conn.execute("INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING upload_id", (pg[1]['user_id'],pg[2],pg[3],pg[4])).fetchone()[0]
    assert datasets.read_dataset_processing(pg[2],pg[3],pg[1])["summary"]["pending"] == 1
    app.dependency_overrides[get_current_user] = lambda: pg[1]
    try:
        client=TestClient(app); url=f"/files/uploads/{upload}/archive"
        assert client.post(url,json={"workspace_id":pg[2]+1,"dataset_id":pg[3]}).status_code == 403
        assert client.post(url,json={"workspace_id":pg[2],"dataset_id":pg[3]+1}).status_code == 403
        app.dependency_overrides[get_current_user] = lambda: {"user_id":pg[1]['user_id']+1,"owner_key":"other"}
        assert client.post(url,json={"workspace_id":pg[2],"dataset_id":pg[3]}).status_code == 403
        app.dependency_overrides[get_current_user] = lambda: pg[1]
        first=client.post(url,json={"workspace_id":pg[2],"dataset_id":pg[3]})
        assert first.status_code == 200
        assert first.json() == client.post(url,json={"workspace_id":pg[2],"dataset_id":pg[3]}).json()
        assert first.json()['archived_by'] == pg[1]['user_id']
    finally:
        app.dependency_overrides.clear()
    assert datasets.read_dataset_processing(pg[2],pg[3],pg[1])["summary"]["total"] == 0
    assert datasets.process_pending(pg[2],pg[3],pg[1])["operation"]["processed"] == 0
    with pytest.raises(DeliveryArchiveConflict): stages.start_processing(upload)
    assert contexts.read_processing_context(upload,pg[1])["archived_at"]


def test_processed_shared_source_preserves_all_history(pipeline):
    db=pipeline[0]; approve_first(pipeline); state=execute(pipeline)
    with db[0]() as conn:
        second=conn.execute("INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING upload_id", (db[1]['user_id'],db[2],db[3],state['file_id'])).fetchone()[0]
        before=[conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ('physical_files','dataset_version_files','processing_attempts','business_rule_answers','approved_rule_policies','data_quality_runs','data_quality_issues','gold_runs','gold_artifacts')]
    objects=dict(pipeline[4]); archive(db,pipeline[2])
    active=datasets.read_dataset_processing(db[2],db[3],db[1])
    assert [d['context']['upload_request_id'] for d in active['deliveries']] == [second]
    assert contexts.read_processing_context(second,db[1])['status'] == 'SUCCESS'
    assert contexts.read_processing_context(pipeline[2],db[1])['archived_at']
    with db[0]() as conn:
        after=[conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ('physical_files','dataset_version_files','processing_attempts','business_rule_answers','approved_rule_policies','data_quality_runs','data_quality_issues','gold_runs','gold_artifacts')]
    assert before==after and objects==pipeline[4]
    assert datasets.process_pending(db[2],db[3],db[1])['operation']['processed']==0
    with pytest.raises(DeliveryArchiveConflict): execute(pipeline)


@pytest.mark.parametrize('ready',[False,True])
def test_rules_and_ready_deliveries_removable(pipeline,ready):
    if ready: approve_first(pipeline)
    before=counts(pipeline[0]); archive(pipeline[0],pipeline[2])
    assert datasets.process_pending(pipeline[0][2],pipeline[0][3],pipeline[0][1])['operation']['processed']==0
    assert before==counts(pipeline[0])
    with pipeline[0][0]() as conn:
        assert conn.execute('SELECT count(*) FROM dataset_version_files').fetchone()[0]>0


def test_running_attempt_and_lifecycle_gap_block_archive(pipeline):
    db=pipeline[0]
    with db[0]() as conn:
        attempt=conn.execute("INSERT INTO processing_attempts(file_id,dataset_version_file_id,attempt_number,stage,status) VALUES (%s,%s,9,'SILVER','PROCESSING') RETURNING attempt_id", (pipeline[3]['file'][0],pipeline[3]['dataset_version_file'][0])).fetchone()[0]
    with pytest.raises(DeliveryArchiveConflict): archive(db,pipeline[2])
    with db[0]() as conn:
        assert conn.execute('SELECT archived_at FROM upload_requests WHERE upload_id=%s',(pipeline[2],)).fetchone()[0] is None
        conn.execute("UPDATE processing_attempts SET status='FAILED' WHERE attempt_id=%s",(attempt,))
    entered,release=Event(),Event()
    def hold():
        with lifecycle_lock(pipeline[2]):
            entered.set(); assert release.wait(10)
    with ThreadPoolExecutor(max_workers=2) as executor:
        running=executor.submit(hold); assert entered.wait(10)
        try:
            with pytest.raises(DeliveryArchiveConflict): archive(db,pipeline[2])
        finally: release.set()
        running.result()
    assert archive(db,pipeline[2])['archived_at']
