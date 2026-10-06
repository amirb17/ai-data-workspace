"""Real PostgreSQL control plane and existing processors; in-memory S3 data plane."""
import io
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries, approve
from app.db import dataset_repository as datasets, file_repository as files
from app.db.processing_repository import get_approved_policy
from app.processing import silver_processor, gold_processor
from app.services import processing_service as stages, processing_context_service as context
from app.services.delivery_execution_service import continue_processing
from app.services import delivery_execution_service as execution
from app.main import app
from app.api.identity import get_current_user

CSV = "id,status,amount\n1,open,10\n,closed,-2\n3,open,20\n"


@pytest.fixture
def pipeline(deliveries, monkeypatch):
    database, upload = deliveries
    objects, calls = {}, {"silver": 0, "gold": 0}
    class Storage:
        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(objects[Key])}
        def put_object(self, Bucket, Key, Body, **kwargs):
            objects[Key] = Body
    monkeypatch.setattr(silver_processor, "s3_client", Storage())
    monkeypatch.setattr(gold_processor, "s3_client", Storage())
    old_bronze = stages.run_bronze_stage
    def bronze(file_id, raw_object_key):
        result = old_bronze(file_id, raw_object_key)
        # Upload fixture uses this known synthetic CSV for initial and second delivery.
        df = pd.read_csv(io.StringIO(CSV if calls.get("second") is None else CSV.replace("3,open,20", "4,open,30")))
        buffer = io.BytesIO(); df.to_parquet(buffer, index=False)
        objects[f"bronze/file_id={file_id}/data.parquet"] = buffer.getvalue()
        return result
    monkeypatch.setattr(stages, "run_bronze_stage", bronze)
    old_silver, old_gold = stages.process_silver, stages.process_gold
    def silver(**kwargs):
        calls["silver"] += 1
        return old_silver(**kwargs)
    def gold(**kwargs):
        calls["gold"] += 1
        return old_gold(**kwargs)
    monkeypatch.setattr(stages, "process_silver", silver)
    monkeypatch.setattr(stages, "process_gold", gold)
    upload_id, result = upload(CSV)
    return database, upload, upload_id, result, objects, calls


def execute(pipeline):
    database, _, upload_id, *_ = pipeline
    return continue_processing(upload_id, database[1], database[2], database[3])


def approve_first(pipeline):
    approve(pipeline[0], pipeline[3])


def counts(database):
    with database[0]() as conn:
        return tuple(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                     for table in ("data_quality_runs", "gold_runs", "gold_artifacts"))


def test_approved_silver_gold_outputs_repeat_and_safe_state(pipeline):
    approve_first(pipeline)
    state = execute(pipeline)
    assert state["status"] == "SUCCESS" and state["rule_version"] == 1
    assert (state["input_rows"], state["valid_rows"], state["rejected_rows"], state["output_rows"]) == (3, 2, 1, 2)
    assert state["quarantine_available"] and state["issue_summary"] == [{"rule_type":"NOT_NULL", "violation_count":1}]
    assert state["duplicate_rows"] is None and state["updated_rows"] is None
    assert state["stages"] == {"bronze":"SUCCESS", "rules":"FINALIZED", "silver":"SUCCESS", "gold":"SUCCESS"}
    database, _, _, result, objects, calls = pipeline
    association = result["dataset_version_file"][0]
    dq = files.get_latest_successful_dq_run_for_dataset_version_file(association)
    assert pd.read_parquet(io.BytesIO(objects[dq[7]]))["id"].tolist() == [1,3]
    quarantine = pd.read_parquet(io.BytesIO(objects[dq[8]]))
    assert len(quarantine) == 1 and not quarantine["_dq_is_valid"].iloc[0]
    gold = files.get_latest_successful_gold_run_for_dataset_version_file(association)
    assert len(pd.read_parquet(io.BytesIO(objects[gold[6]]))) == 2
    assert stages._is_gold_publication_complete(gold[0], result["file"][0])
    before = counts(database), set(objects)
    assert execute(pipeline)["status"] == "SUCCESS"
    assert stages.run_silver_processing(association, "test")["already_processed"]
    assert stages.run_gold_processing(association, "test")["already_processed"]
    assert before == (counts(database), set(objects))
    assert calls["silver"] == calls["gold"] == 1
    assert state["completed_at"] and state["latest_attempt"]["stage"] == "GOLD"
    assert not any(term in str(state) for term in ("s3://", "storage_path", "silver_path", "quarantine_path", "gold_path", "Traceback"))
    stages.start_processing(pipeline[2])
    assert context.read_processing_context(pipeline[2], database[1])["status"] == "SUCCESS"


def test_unapproved_silver_and_gold_before_silver_are_blocked(pipeline):
    association = pipeline[3]["dataset_version_file"][0]
    with pytest.raises(ValueError, match="Approved"):
        execute(pipeline)
    approve_first(pipeline)
    with pytest.raises(ValueError, match="No successful Silver"):
        stages.run_gold_processing(association, "test")
    assert pipeline[5]["silver"] == pipeline[5]["gold"] == 0


def test_same_schema_second_delivery_reuses_rules_end_to_end(pipeline):
    approve_first(pipeline); execute(pipeline)
    database, upload, first_id, first, _, calls = pipeline
    calls["second"] = True
    second_id, second = upload(CSV.replace("3,open,20", "4,open,30"))
    assert second_id != first_id and second["file"][0] != first["file"][0]
    before = context.read_processing_context(second_id, database[1])
    assert before["rules_reused"] and before["status"] == "READY_FOR_SILVER"
    state = continue_processing(second_id, database[1])
    assert state["status"] == "SUCCESS" and state["rule_version"] == 1
    assert state["dataset_version_id"] == first["dataset_version"]["dataset_version_id"]
    assert calls["silver"] == calls["gold"] == 2


@pytest.mark.parametrize("stage", ["silver", "gold"])
def test_stage_failure_retry_does_not_repeat_successful_stage(pipeline, monkeypatch, stage):
    approve_first(pipeline)
    original = getattr(stages, "process_"+stage)
    def fail(**kwargs): raise RuntimeError("private infrastructure / internal path")
    monkeypatch.setattr(stages, "process_"+stage, fail)
    with pytest.raises(RuntimeError): execute(pipeline)
    state = context.read_processing_context(pipeline[2], pipeline[0][1])
    assert state["status"] == stage.upper()+"_FAILED"
    assert "private" not in str(state) and state["error_summary"]
    if stage == "gold":
        assert state["valid_rows"] == 2 and state["stages"]["silver"] == "SUCCESS"
    monkeypatch.setattr(stages, "process_"+stage, original)
    assert execute(pipeline)["status"] == "SUCCESS"
    assert pipeline[5]["silver"] == pipeline[5]["gold"] == 1


@pytest.mark.parametrize("stage", ["silver", "gold"])
def test_metadata_failure_rolls_back_and_retries_without_duplicate_runs(pipeline, monkeypatch, stage):
    approve_first(pipeline)
    method = "save_gold_artifact" if stage == "gold" else "save_data_quality_issue"
    original = getattr(stages, method)
    monkeypatch.setattr(stages, method, lambda **_: (_ for _ in ()).throw(RuntimeError("metadata failed")))
    with pytest.raises(RuntimeError): execute(pipeline)
    assert counts(pipeline[0]) == ((1,0,0) if stage == "gold" else (0,0,0))
    monkeypatch.setattr(stages, method, original)
    assert execute(pipeline)["status"] == "SUCCESS"
    assert pipeline[5]["silver"] == (1 if stage == "gold" else 2)
    assert counts(pipeline[0])[0:2] == (1,1)


def test_historical_pin_executes_snapshot_after_newer_mutable_draft(pipeline):
    approve_first(pipeline)
    database = pipeline[0]; version = pipeline[3]["dataset_version"]["dataset_version_id"]
    with database[0]() as conn:
        conn.execute("UPDATE dataset_versions SET rule_version=2 WHERE dataset_version_id=%s", (version,))
        conn.execute("UPDATE business_rules SET is_active=FALSE WHERE dataset_version_id=%s", (version,))
    assert get_approved_policy(version, 1)
    historical = execute(pipeline)
    assert historical["rule_version"] == 1
    assert historical["active_rule_count"] == len(get_approved_policy(version, 1))
    assert execute(pipeline)["status"] == "SUCCESS"
    assert pipeline[5]["silver"] == pipeline[5]["gold"] == 1


def test_concurrent_request_is_rejected_and_running_state_is_visible(pipeline, monkeypatch):
    approve_first(pipeline)
    entered, release = Event(), Event()
    original = stages.process_silver
    def slow(**kwargs):
        entered.set()
        assert release.wait(15)
        return original(**kwargs)
    monkeypatch.setattr(stages, "process_silver", slow)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(execute, pipeline)
        try:
            assert entered.wait(10)
            with pytest.raises(RuntimeError, match="already active"): execute(pipeline)
            state = context.read_processing_context(pipeline[2], pipeline[0][1])
            assert state["status"] == "SILVER_PROCESSING" and state["stages"]["silver"] == "PROCESSING"
        finally: release.set()
        assert first.result()["status"] == "SUCCESS"
    assert pipeline[5]["silver"] == pipeline[5]["gold"] == 1


def test_interrupted_attempt_recovers_without_reupload_or_bronze(pipeline):
    approve_first(pipeline)
    association = pipeline[3]["dataset_version_file"][0]
    attempt = files.create_processing_attempt(pipeline[3]["file"][0], 1, "SILVER", "PROCESSING", association)
    datasets.update_dataset_version_file_status(association, "SILVER_PROCESSING")
    assert execute(pipeline)["status"] == "SUCCESS"
    with pipeline[0][0]() as conn:
        assert conn.execute("SELECT status FROM processing_attempts WHERE attempt_id=%s", (attempt[0],)).fetchone()[0] == "CRASHED"


def test_interrupted_recovery_metadata_failure_is_atomic(pipeline, monkeypatch):
    approve_first(pipeline)
    association = pipeline[3]["dataset_version_file"][0]
    attempt = files.create_processing_attempt(pipeline[3]["file"][0], 1, "SILVER", "PROCESSING", association)
    datasets.update_dataset_version_file_status(association, "SILVER_PROCESSING")
    original = execution.update_dataset_version_file_status
    monkeypatch.setattr(execution, "update_dataset_version_file_status", lambda *args: (_ for _ in ()).throw(RuntimeError("Recovery metadata failed")))
    with pytest.raises(RuntimeError): execute(pipeline)
    with pipeline[0][0]() as conn:
        assert conn.execute("SELECT status FROM processing_attempts WHERE attempt_id=%s", (attempt[0],)).fetchone()[0] == "PROCESSING"
    monkeypatch.setattr(execution, "update_dataset_version_file_status", original)
    assert execute(pipeline)["status"] == "SUCCESS"


def test_legacy_partial_gold_publication_recovers_same_run(pipeline):
    approve_first(pipeline)
    result = pipeline[3]; association = result["dataset_version_file"][0]
    silver = stages.run_silver_processing(association, "test")
    attempt = files.create_processing_attempt(result["file"][0], 2, "GOLD", "FAILED", association)
    path = f"gold/base/dataset_version_id={result['dataset_version']['dataset_version_id']}/file_id={result['file'][0]}/rule_version=1/data.parquet"
    run = files.save_gold_run(result["file"][0], attempt[0], silver["dq_run_id"], "BASE", 2, path, association)
    datasets.update_dataset_version_file_status(association, "GOLD_FAILED")
    assert execute(pipeline)["status"] == "SUCCESS"
    assert files.get_latest_successful_gold_run_for_dataset_version_file(association)[0] == run[0]
    assert counts(pipeline[0])[0:2] == (1,1)
    assert pipeline[5]["silver"] == pipeline[5]["gold"] == 1


def test_owned_routes_safe_responses_and_legacy_mutation_cannot_regress_success(pipeline):
    approve_first(pipeline)
    database, _, upload_id, result, *_ = pipeline
    app.dependency_overrides[get_current_user] = lambda: database[1]
    try:
        with TestClient(app) as client:
            association = result["dataset_version_file"][0]
            for path in [f"/files/uploads/{upload_id}/continue", f"/files/dataset-version-files/{association}/silver/process", f"/files/dataset-version-files/{association}/gold/process"]:
                assert client.post(path+f"?workspace_id={database[2]+100}&dataset_id={database[3]}").status_code == 403
                assert client.post(path+f"?workspace_id={database[2]}&dataset_id={database[3]+100}").status_code == 403
            assert client.post(f"/files/uploads/{upload_id}/continue").json()["status"] == "SUCCESS"
            response = client.get(f"/files/uploads/{upload_id}/processing-context")
            assert response.status_code == 200 and "path" not in response.text
            mutation = client.patch(f"/files/dataset-version-files/{association}/business-rules", json={"column_name":"id","rule_type":"NOT_NULL","answer":"NO","expected_rule_version":1})
            assert mutation.status_code == 409
            assert client.get(f"/files/uploads/{upload_id}/processing-context").json()["status"] == "SUCCESS"
            app.dependency_overrides[get_current_user] = lambda: {"user_id":database[1]["user_id"]+100,"owner_key":"other"}
            assert client.post(f"/files/uploads/{upload_id}/continue").status_code == 403
    finally: app.dependency_overrides.pop(get_current_user, None)


def test_legacy_stage_rejects_owned_but_schema_mismatched_association(pipeline):
    database = pipeline[0]
    with database[0]() as conn:
        version = conn.execute("INSERT INTO dataset_versions(dataset_id,version_number,schema_hash) VALUES (%s,100,%s) RETURNING dataset_version_id", (database[3], 'c'*64)).fetchone()[0]
        association = conn.execute("INSERT INTO dataset_version_files(dataset_version_id,file_id,status) VALUES (%s,%s,'READY_FOR_SILVER') RETURNING dataset_version_file_id", (version,pipeline[3]["file"][0])).fetchone()[0]
    app.dependency_overrides[get_current_user] = lambda: database[1]
    try:
        with TestClient(app) as client:
            for stage in ("silver", "gold"):
                assert client.post(f"/files/dataset-version-files/{association}/{stage}/process").status_code == 400
        assert pipeline[5]["silver"] == pipeline[5]["gold"] == 0
    finally: app.dependency_overrides.pop(get_current_user, None)
