import hashlib
import io

import pandas as pd
import pytest

from tests.test_rule_approval_postgres import pg
from app.db import dataset_repository as datasets, file_repository as files
from app.services import processing_service, business_rule_service as rules, rule_reuse_service as reuse, processing_context_service as context
from app.services.rule_suggestion_service import generate_rule_suggestions
from app.processing.dataset_profiler import profile_dataframe
from app.processing.schema_fingerprint import calculate_schema_hash
from app.schemas.business_rules import BusinessRuleAnswer


@pytest.fixture
def deliveries(pg, monkeypatch):
    connect, user, workspace, dataset, *_ = pg
    contents = {}
    monkeypatch.setattr(processing_service, "parse_s3_uri", lambda _: "raw/test.csv")
    monkeypatch.setattr(rules, "generate_rule_suggestions", generate_rule_suggestions)
    monkeypatch.setattr(reuse, "generate_rule_suggestions", generate_rule_suggestions)
    def bronze(file_id, raw_object_key):
        df = pd.read_csv(io.StringIO(contents[file_id]))
        return {"schema_hash": calculate_schema_hash(df), "row_count": len(df), "column_names": list(df.columns),
                "profiles": profile_dataframe(df), "bronze_object_key": "test-only", "schema": {c:str(t) for c,t in df.dtypes.items()}}
    monkeypatch.setattr(processing_service, "run_bronze_stage", bronze)
    def upload(csv, target_dataset=None, target_workspace=None):
        with connect() as conn:
            file_id = conn.execute("INSERT INTO physical_files(file_name,file_size,file_hash,storage_path,status) VALUES ('test.csv',%s,%s,'s3://test-only/raw/test.csv','UPLOADED') RETURNING file_id", (len(csv), hashlib.sha256(csv.encode()).hexdigest())).fetchone()[0]
            upload_id = conn.execute("INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING upload_id", (user["user_id"], target_workspace or workspace, target_dataset or dataset, file_id)).fetchone()[0]
        contents[file_id] = csv
        return upload_id, processing_service.start_processing(upload_id)
    return pg, upload


def approve(pg, result):
    _, user, ws, ds, _, _, _ = pg
    association = result["dataset_version_file"][0]
    questions = generate_rule_suggestions(result["file"][0])
    # Explicit synthetic policy for this fixture, not inferred user answers.
    answers = [BusinessRuleAnswer(column_name=q["column_name"], rule_type=q["suggested_rule_type"],
                                 answer="DECIMAL" if q["suggested_rule_type"] == "DATA_TYPE" else "YES") for q in questions]
    with context.rule_mutation(association, user, 0, ws, ds):
        rules.submit_business_rule_answers(association, answers)
    with context.rule_mutation(association, user, 1, ws, ds):
        assert rules.finalize_business_rules(association)["finalized"]


def test_first_approval_second_delivery_reuse_and_repeat(deliveries):
    pg, upload = deliveries
    first_upload, first = upload("id,name\n1,Alice\n")
    assert first["dataset_version_file"][3] == "AWAITING_RULES"
    approve(pg, first)
    second_upload, second = upload("id,name\n2,Bob\n3,Cara\n")
    assert first_upload != second_upload and first["file"][0] != second["file"][0]
    assert second["dataset_version"]["dataset_version_id"] == first["dataset_version"]["dataset_version_id"]
    assert second["dataset_version_file"][0] != first["dataset_version_file"][0]
    assert second["dataset_version_file"][3] == "READY_FOR_SILVER"
    before_answers = files.get_business_rule_answers_for_dataset_version(second["dataset_version"]["dataset_version_id"])
    processing_service.start_processing(second_upload)
    state = context.read_processing_context(second_upload, pg[1], pg[2], pg[3])
    assert state["rule_version"] == 1 and state["rules_reused"] and state["silver_can_proceed"]
    assert state["status"] == "READY_FOR_SILVER"
    assert files.get_business_rule_answers_for_dataset_version(second["dataset_version"]["dataset_version_id"]) == before_answers


@pytest.mark.parametrize("csv", ["id,name,extra\n2,Bob,new\n", "id,name\ntext,Bob\n", "name\nBob\n"])
def test_changed_schema_gets_separate_review(deliveries, csv):
    pg, upload = deliveries
    _, first = upload("id,name\n1,Alice\n")
    approve(pg, first)
    _, changed = upload(csv)
    assert changed["dataset_version"]["dataset_version_id"] != first["dataset_version"]["dataset_version_id"]
    assert changed["dataset_version_file"][3] == "AWAITING_RULES"


@pytest.mark.parametrize("other_workspace", [False, True])
def test_same_schema_other_workspace_dataset_never_reuses(deliveries, other_workspace):
    pg, upload = deliveries
    _, first = upload("id,name\n1,Alice\n")
    approve(pg, first)
    with pg[0]() as conn:
        ws = conn.execute("INSERT INTO workspaces(workspace_name,owner) VALUES ('Other','dev:test') RETURNING workspace_id").fetchone()[0] if other_workspace else pg[2]
        ds = conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES (%s,'Other','dev:test') RETURNING dataset_id", (ws,)).fetchone()[0]
    _, other = upload("id,name\n2,Bob\n", ds, ws)
    assert other["dataset_version_file"][3] == "AWAITING_RULES"
    assert not files.get_active_business_rules_for_dataset_version(other["dataset_version"]["dataset_version_id"])


@pytest.mark.parametrize("failure", ["draft", "stale", "inactive", "unapproved"])
def test_nonapproved_or_stale_rules_not_reused(deliveries, failure):
    pg, upload = deliveries
    first_upload, first = upload("id,name\n1,Alice\n")
    if failure != "draft":
        approve(pg, first)
    else:
        with context.rule_mutation(first["dataset_version_file"][0], pg[1], 0, pg[2], pg[3]):
            rules.submit_business_rule_answers(first["dataset_version_file"][0], [BusinessRuleAnswer(column_name="id", rule_type="NOT_NULL", answer="YES")])
    version = first["dataset_version"]["dataset_version_id"]
    with pg[0]() as conn:
        if failure == "stale": conn.execute("UPDATE dataset_versions SET rule_version=rule_version+1 WHERE dataset_version_id=%s", (version,))
        if failure == "inactive": conn.execute("UPDATE business_rules SET is_active=FALSE WHERE dataset_version_id=%s", (version,))
        if failure == "unapproved": conn.execute("UPDATE dataset_versions SET approved_rule_version=NULL WHERE dataset_version_id=%s", (version,))
    _, second = upload("id,name\n2,Bob\n")
    assert second["dataset_version_file"][3] == "AWAITING_RULES"
    assert datasets.get_rule_approval_context(second["dataset_version_file"][0])[3] is None
    if failure != "draft":
        assert datasets.get_rule_approval_context(first["dataset_version_file"][0])[3] == 1  # historical pin preserved
    if failure == "stale":
        historical = context.read_processing_context(first_upload, pg[1], pg[2], pg[3])
        assert historical["rule_version"] == 1 and historical["current_rule_version"] == 2
        assert historical["silver_can_proceed"] is False


def test_new_profile_question_requires_explicit_review(deliveries):
    pg, upload = deliveries
    _, first = upload("id,status\n1,open\n")
    approve(pg, first)
    _, second = upload("id,status\n2,open\n3,\n")
    assert second["dataset_version"]["dataset_version_id"] == first["dataset_version"]["dataset_version_id"]
    assert second["dataset_version_file"][3] == "AWAITING_RULES"


def test_coverage_rejects_cross_version_rows_and_mismatched_configs():
    question = {"column_name":"id", "suggested_rule_type":"NOT_NULL", "options":["YES","NO"]}
    approval = (50, 1, 1, None, False, True)
    answer = (1, 50, "id", "NOT_NULL", "YES")
    active = (1, 50, "id", "NOT_NULL", {"required":True}, True)
    assert reuse.approved_rules_cover_questions(approval, [question], [answer], [active])
    assert not reuse.approved_rules_cover_questions(approval, [question], [answer], [(1,51,*active[2:])])
    assert not reuse.approved_rules_cover_questions(approval, [question], [answer], [(1,50,"id","NOT_NULL",{},True)])
    assert not reuse.approved_rules_cover_questions((50,1,1,None,False,False), [question], [answer], [active])
