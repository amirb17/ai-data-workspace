"""Independent delivery orchestration and zero-valid publication over real PostgreSQL."""
import io
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline, approve_first, execute, counts, CSV
from app.services import processing_service as stages, processing_context_service as contexts
from app.services import dataset_processing_service as datasets
from app.services.delivery_execution_service import continue_processing
from app.db import file_repository as files
from app.main import app
from app.api.identity import get_current_user


def source(pipeline, result, csv):
    buffer = io.BytesIO()
    pd.read_csv(io.StringIO(csv)).to_parquet(buffer, index=False)
    pipeline[4][f"bronze/file_id={result['file'][0]}/data.parquet"] = buffer.getvalue()


def pending(pipeline):
    db = pipeline[0]
    return datasets.process_pending(db[2], db[3], db[1])


def attempt_count(db):
    with db[0]() as conn:
        return conn.execute("SELECT count(*) FROM processing_attempts").fetchone()[0]


@pytest.mark.parametrize("all_rejected", [False, True])
def test_zero_valid_and_all_valid_terminal_refresh_and_repeat(pipeline, all_rejected):
    approve_first(pipeline)
    csv = "id,status,amount\n,open,10\n,closed,-2\n,open,20\n" if all_rejected else CSV.replace(",closed,-2", "2,closed,-2")
    source(pipeline, pipeline[3], csv)
    state = execute(pipeline)
    assert state["status"] == ("SUCCESS_WITH_WARNINGS" if all_rejected else "SUCCESS")
    assert state["valid_rows"] == (0 if all_rejected else 3)
    assert state["rejected_rows"] == (3 if all_rejected else 0)
    assert state["output_rows"] == state["valid_rows"]
    assert state["stages"]["silver"] == "SUCCESS"
    assert state["stages"]["gold"] == ("SKIPPED" if all_rejected else "SUCCESS")
    assert not state["error_summary"] and not state["can_continue"] and state["completed_at"]
    assert pipeline[5]["gold"] == (0 if all_rejected else 1)
    if all_rejected:
        assert state["gold_skip_reason"] == "No valid rows were available for Gold publication."
        assert counts(pipeline[0]) == (1, 0, 0)
        dq = files.get_latest_successful_dq_run_for_dataset_version_file(state["dataset_version_file_id"])
        assert len(pd.read_parquet(io.BytesIO(pipeline[4][dq[8]]))) == 3
    before = counts(pipeline[0]), attempt_count(pipeline[0]), dict(pipeline[4])
    for _ in range(2):
        assert execute(pipeline)["status"] == state["status"]
        stages.run_silver_processing(state["dataset_version_file_id"], "test")
        stages.run_gold_processing(state["dataset_version_file_id"], "test")
        stages.start_processing(pipeline[2])
        pending(pipeline)
    assert before == (counts(pipeline[0]), attempt_count(pipeline[0]), dict(pipeline[4]))
    refreshed = contexts.read_processing_context(pipeline[2], pipeline[0][1])
    for key in ("status", "stages", "valid_rows", "rejected_rows", "gold_skip_reason", "completed_at"):
        assert refreshed[key] == state[key]


def test_two_pending_reuse_separate_identities_skip_success_and_repeat(pipeline):
    approve_first(pipeline)
    second_id, second = pipeline[1](CSV.replace("3,open,20", "4,open,30"))
    source(pipeline, second, CSV.replace("3,open,20", "4,open,30"))
    db = pipeline[0]
    initial = datasets.read_dataset_processing(db[2], db[3], db[1])
    assert initial["summary"]["pending"] == 2
    assert all(d["size_bytes"] > 0 for d in initial["deliveries"])
    result = pending(pipeline)
    assert result["operation"] == {"processed": 2, "successful": 2, "needs_attention": 0,
        "outcomes": [{"upload_request_id": pipeline[2], "status": "SUCCESS", "needs_attention": False},
                     {"upload_request_id": second_id, "status": "SUCCESS", "needs_attention": False}]}
    states = [d["context"] for d in result["deliveries"]]
    assert len({s["dataset_version_file_id"] for s in states}) == 2
    assert len({s["file_id"] for s in states}) == 2
    assert states[1]["rules_reused"] and all(s["rule_version"] == 1 for s in states)
    before = counts(db), attempt_count(db), dict(pipeline[4])
    assert pending(pipeline)["operation"]["processed"] == 0
    assert before == (counts(db), attempt_count(db), dict(pipeline[4]))
    third_id, third = pipeline[1](CSV.replace("3,open,20", "5,open,40"))
    source(pipeline, third, CSV.replace("3,open,20", "5,open,40"))
    next_result = pending(pipeline)
    assert next_result["operation"]["processed"] == 1
    assert next_result["operation"]["outcomes"][0]["upload_request_id"] == third_id
    assert next_result["summary"]["successful"] == 3


def test_awaiting_rules_does_not_block_ready_delivery(pipeline):
    approve_first(pipeline)
    awaiting_id, awaiting = pipeline[1]("id,status,amount,extra\n2,open,10,new\n")
    result = pending(pipeline)
    assert result["operation"]["processed"] == 1
    assert result["summary"]["awaiting_rules"] == 1
    state = contexts.read_processing_context(awaiting_id, pipeline[0][1])
    assert state["status"] == "AWAITING_RULES" and state["rule_version"] == 0
    assert not files.get_active_business_rules_for_dataset_version(awaiting["dataset_version"]["dataset_version_id"])


def test_failed_delivery_does_not_rollback_others_or_auto_retry(pipeline, monkeypatch):
    approve_first(pipeline)
    second_id, second = pipeline[1](CSV.replace("3,open,20", "4,open,30"))
    third_id, third = pipeline[1](CSV.replace("3,open,20", "5,open,40"))
    original = stages.process_silver
    def silver(**kwargs):
        if kwargs["file_id"] == second["file"][0]:
            raise RuntimeError("synthetic storage failure")
        return original(**kwargs)
    monkeypatch.setattr(stages, "process_silver", silver)
    result = pending(pipeline)
    assert result["operation"]["processed"] == 3 and result["operation"]["successful"] == 2
    assert result["operation"]["needs_attention"] == 1
    assert contexts.read_processing_context(third_id, pipeline[0][1])["status"] == "SUCCESS"
    assert contexts.read_processing_context(second_id, pipeline[0][1])["status"] == "SILVER_FAILED"
    before = counts(pipeline[0]), attempt_count(pipeline[0])
    assert pending(pipeline)["operation"]["processed"] == 0
    assert before == (counts(pipeline[0]), attempt_count(pipeline[0]))


@pytest.mark.parametrize("other_workspace", [False, True])
def test_dataset_scope_and_api_ownership(pipeline, other_workspace):
    approve_first(pipeline)
    db = pipeline[0]
    with db[0]() as conn:
        ws = conn.execute("INSERT INTO workspaces(workspace_name,owner) VALUES ('Other','dev:test') RETURNING workspace_id").fetchone()[0] if other_workspace else db[2]
        ds = conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES (%s,'Other','dev:test') RETURNING dataset_id", (ws,)).fetchone()[0]
    other_id, _ = pipeline[1](CSV.replace("3,open,20", "8,open,40"), ds, ws)
    before = contexts.read_processing_context(other_id, db[1])
    pending(pipeline)
    assert contexts.read_processing_context(other_id, db[1]) == before
    app.dependency_overrides[get_current_user] = lambda: db[1]
    try:
        client = TestClient(app)
        url = f"/workspaces/{db[2]}/datasets/{db[3]}/processing"
        assert client.get(url).json()["summary"]["successful"] == 1
        assert client.post(url+"/pending").json()["operation"]["processed"] == 0
        if other_workspace:
            assert client.post(f"/workspaces/{db[2]}/datasets/{ds}/processing/pending").status_code == 403
        app.dependency_overrides[get_current_user] = lambda: {"user_id": db[1]["user_id"]+1, "owner_key": "other"}
        assert client.get(url).status_code == 403 and client.post(url+"/pending").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_dataset_concurrency_refuses_duplicate_work(pipeline, monkeypatch):
    approve_first(pipeline)
    entered, release = Event(), Event()
    original = stages.process_silver
    def slow(**kwargs):
        entered.set()
        assert release.wait(10)
        return original(**kwargs)
    monkeypatch.setattr(stages, "process_silver", slow)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(pending, pipeline)
        assert entered.wait(10)
        try:
            with pytest.raises(RuntimeError, match="already active"):
                pending(pipeline)
        finally:
            release.set()
        assert first.result()["operation"]["successful"] == 1
    assert pipeline[5]["silver"] == pipeline[5]["gold"] == 1


def test_ready_to_process_runs_bronze_and_stops_at_new_rules(pipeline):
    db = pipeline[0]
    # Remove only synthetic fixture Bronze metadata and association; source remains accepted.
    with db[0]() as conn:
        conn.execute("DELETE FROM dataset_version_files WHERE dataset_version_file_id=%s", (pipeline[3]["dataset_version_file"][0],))
        conn.execute("UPDATE physical_files SET schema_hash=NULL WHERE file_id=%s", (pipeline[3]["file"][0],))
    assert datasets.read_dataset_processing(db[2], db[3], db[1])["summary"]["pending"] == 1
    result = pending(pipeline)
    assert result["operation"]["processed"] == 1 and result["summary"]["awaiting_rules"] == 1
    assert result["deliveries"][0]["context"]["status"] == "AWAITING_RULES"
    assert pipeline[5]["silver"] == pipeline[5]["gold"] == 0
