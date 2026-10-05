"""RUN_DB_TESTS=1: isolated PostgreSQL approval/rollback/concurrency checks; no S3 calls."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.db import database, dataset_repository, file_repository
from app.services import business_rule_service as rules, processing_context_service as context, processing_service
from app.schemas.business_rules import BusinessRuleAnswer


@pytest.fixture
def pg(monkeypatch):
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Set RUN_DB_TESTS=1 for isolated PostgreSQL tests")
    original_connect = psycopg.connect
    schema = "test_phase5a_" + uuid4().hex
    with original_connect(database.DATABASE_URL) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    def connect(*args, **kwargs):
        return original_connect(database.DATABASE_URL, options=f"-c search_path={schema}")
    try:
        with connect() as conn:
            for path in sorted(Path("migrations").glob("*.sql")):
                conn.execute(path.read_text())
            user = conn.execute("INSERT INTO users(owner_key,display_name) VALUES ('dev:test','Test') RETURNING user_id").fetchone()[0]
            ws = conn.execute("INSERT INTO workspaces(workspace_name,owner) VALUES ('Test','dev:test') RETURNING workspace_id").fetchone()[0]
            ds = conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES (%s,'Orders','dev:test') RETURNING dataset_id", (ws,)).fetchone()[0]
            file = conn.execute("INSERT INTO physical_files(file_name,file_size,file_hash,storage_path,status,schema_hash) VALUES ('a.csv',10,%s,'test-only','UPLOADED',%s) RETURNING file_id", ('a'*64, 'b'*64)).fetchone()[0]
            version = conn.execute("INSERT INTO dataset_versions(dataset_id,version_number,schema_hash) VALUES (%s,1,%s) RETURNING dataset_version_id", (ds, 'b'*64)).fetchone()[0]
            association = conn.execute("INSERT INTO dataset_version_files(dataset_version_id,file_id,status) VALUES (%s,%s,'AWAITING_RULES') RETURNING dataset_version_file_id", (version,file)).fetchone()[0]
        monkeypatch.setattr(database.psycopg, "connect", connect)
        monkeypatch.setattr(rules, "generate_rule_suggestions", lambda _: [{"column_name":"id","suggested_rule_type":"NOT_NULL","options":["YES","NO"]}])
        yield connect, {"user_id":user,"owner_key":"dev:test"}, ws, ds, file, version, association
    finally:
        with original_connect(database.DATABASE_URL) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def answer():
    return BusinessRuleAnswer(column_name="id", rule_type="NOT_NULL", answer="YES")


def test_transaction_rollback_removes_partial_answers_and_version(pg, monkeypatch):
    connect, user, ws, ds, file, version, association = pg
    monkeypatch.setattr(rules, "save_business_rule_for_dataset_version", lambda **kw: (_ for _ in ()).throw(RuntimeError("simulated failure")))
    with pytest.raises(RuntimeError):
        with context.rule_mutation(association, user, 0, ws, ds):
            rules.submit_business_rule_answers(association, [answer()])
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM business_rule_answers").fetchone()[0] == 0
        assert conn.execute("SELECT rule_version FROM dataset_versions WHERE dataset_version_id=%s", (version,)).fetchone()[0] == 0


def test_concurrent_rule_saves_allow_only_current_version(pg):
    _, user, ws, ds, file, version, association = pg
    def save():
        try:
            with context.rule_mutation(association, user, 0, ws, ds):
                rules.submit_business_rule_answers(association, [answer()])
            return "saved"
        except context.StaleRuleVersion:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: save(), range(2)))
    assert sorted(results) == ["saved", "stale"]
    assert dataset_repository.get_dataset_version_rule_version(version) == 1
    assert len(file_repository.get_active_business_rules_for_dataset_version(version)) == 1


def test_finalize_and_refresh_preserve_approved_answers(pg):
    _, user, ws, ds, file, version, association = pg
    with context.rule_mutation(association, user, 0, ws, ds):
        rules.submit_business_rule_answers(association, [answer()])
    with context.rule_mutation(association, user, 1, ws, ds):
        assert rules.finalize_business_rules(association)["finalized"]
    with context.rule_mutation(association, user, 1, ws, ds):
        assert rules.finalize_business_rules(association)["already_finalized"]
    assert dataset_repository.get_dataset_version_file_by_id(association)[3] == "READY_FOR_SILVER"
    assert file_repository.get_business_rule_answers_for_dataset_version(version)[0][4] == "YES"


@pytest.mark.parametrize("status", ["UPLOADED", "READY_FOR_SILVER", "SILVER_PROCESSING", "READY_FOR_GOLD", "GOLD_PROCESSING", "SUCCESS", "SILVER_FAILED", "GOLD_FAILED"])
def test_real_repeated_bronze_conditional_update(pg, status):
    connect, user, ws, ds, file, version, association = pg
    with connect() as conn:
        upload = conn.execute("INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING upload_id", (user["user_id"],ws,ds,file)).fetchone()[0]
    dataset_repository.update_dataset_version_file_status(association, status)
    result = processing_service.start_processing(upload)
    assert result["dataset_version_file"][3] == ("AWAITING_RULES" if status == "UPLOADED" else status)
    assert result["attempt"] is None
