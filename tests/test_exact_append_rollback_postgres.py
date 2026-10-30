"""Genuine two-source Exact APPEND rollback/retry in an isolated PostgreSQL schema."""

import hashlib
import io
import json
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from psycopg.rows import dict_row

from app.config import S3_BUCKET_NAME
from app.db.processing_repository import get_approved_policy
from app.processing import bronze_processor, silver_processor
from app.schemas.business_rules import BusinessRuleAnswer
from app.schemas.incremental import LoadPolicyRequest
from app.services import append_application_service as execution
from app.services import business_rule_service as rules
from app.services import processing_context_service as context
from app.services import processing_service as stages
from app.services import rule_reuse_service as reuse
from app.services.incremental_policy_service import save_policy
from app.services.rule_suggestion_service import generate_rule_suggestions
from app.storage.incremental_artifacts import IncrementalArtifacts
from tests.test_exact_pipeline_e2e import MemoryS3
from tests.test_rule_approval_postgres import pg


class AuditedMemoryS3(MemoryS3):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.writes = []
        self.forbidden_reads = set()

    def get_object(self, Bucket, Key):
        assert Key not in self.forbidden_reads, "Retry read mutable source Silver"
        self.reads.append(Key)
        return super().get_object(Bucket=Bucket, Key=Key)

    def put_object(self, Bucket, Key, Body, **kwargs):
        assert Key not in self.objects, "Existing artifact overwritten"
        self.writes.append(Key)
        return super().put_object(Bucket=Bucket, Key=Key, Body=Body, **kwargs)


def test_two_real_exact_deliveries_rollback_after_publication_and_retry(pg, monkeypatch):
    connect, user, workspace_id, dataset_id, *_ = pg
    storage = AuditedMemoryS3()
    store = IncrementalArtifacts(storage)
    monkeypatch.setattr(bronze_processor, "get_s3_client", lambda: storage)
    monkeypatch.setattr(silver_processor, "s3_client", storage)
    monkeypatch.setattr(execution, "IncrementalArtifacts", lambda: store)
    # Undo only the pg fixture's synthetic suggestion generator. Approval,
    # approved-rule resolution, Bronze, Silver and publication stay genuine.
    monkeypatch.setattr(rules, "generate_rule_suggestions", generate_rule_suggestions)
    monkeypatch.setattr(reuse, "generate_rule_suggestions", generate_rule_suggestions)
    monkeypatch.setenv("DATARISE_EXACT_APPEND_V2_TEST_ONLY", "1")

    def rows(query, params=()):
        # A separate connection observes committed data, including while the
        # publication wrapper below is inside the real write transaction.
        with connect() as conn:
            return conn.cursor(row_factory=dict_row).execute(query, params).fetchall()

    def head():
        return rows(
            "SELECT current_state_id, current_gold_run_id, state_analytics_status "
            "FROM datasets WHERE dataset_id=%s", (dataset_id,),
        )[0]

    def applications():
        return rows(
            "SELECT * FROM delivery_applications WHERE dataset_id=%s "
            "ORDER BY application_id", (dataset_id,),
        )

    def states():
        return rows(
            "SELECT * FROM dataset_state_versions WHERE dataset_id=%s "
            "ORDER BY state_version", (dataset_id,),
        )

    def manifests():
        return rows(
            "SELECT dm.* FROM delivery_manifests dm JOIN delivery_applications a "
            "ON a.application_id=dm.application_id WHERE a.dataset_id=%s "
            "ORDER BY dm.manifest_id", (dataset_id,),
        )

    def register_and_bronze(name, csv):
        payload = csv.encode("utf-8")
        raw_key = f"raw/{name}"
        storage.objects[raw_key] = payload
        with connect() as conn:
            file_id = conn.execute(
                "INSERT INTO physical_files(file_name,file_size,file_hash,storage_path,status) "
                "VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING file_id",
                (name, len(payload), hashlib.sha256(payload).hexdigest(),
                 f"s3://{S3_BUCKET_NAME}/{raw_key}"),
            ).fetchone()[0]
            upload_id = conn.execute(
                "INSERT INTO upload_requests(user_id,workspace_id,dataset_id,file_id,status) "
                "VALUES (%s,%s,%s,%s,'UPLOADED') RETURNING upload_id",
                (user["user_id"], workspace_id, dataset_id, file_id),
            ).fetchone()[0]
        result = stages.start_processing(upload_id)
        assert raw_key in storage.reads
        for artifact in ("data.parquet", "raw_text.parquet"):
            assert f"bronze/file_id={file_id}/{artifact}" in storage.objects
        return upload_id, file_id, result

    def assert_decimal_artifact(key, expected):
        table = pq.read_table(io.BytesIO(storage.objects[key]))
        assert table.schema.field("amount").type == pa.decimal128(20, 4)
        values = table.column("amount").to_pylist()
        assert values == expected
        assert all(isinstance(v, Decimal) and v.as_tuple().exponent == -4 for v in values)

    def silver_for(result, expected):
        silver = stages.run_silver_processing(result["dataset_version_file"][0], S3_BUCKET_NAME)
        assert silver["status"] == "SUCCESS"
        assert (silver["total_rows"], silver["valid_rows"], silver["rejected_rows"]) == (2, 2, 0)
        assert_decimal_artifact(silver["silver_path"], expected)
        dq = rows("SELECT * FROM data_quality_runs WHERE dq_run_id=%s", (silver["dq_run_id"],))[0]
        assert dq["silver_sha256"] == hashlib.sha256(storage.objects[silver["silver_path"]]).hexdigest()
        return silver

    def apply(upload_id):
        return execution.apply_incremental(workspace_id, dataset_id, user, upload_id)

    first_values = [Decimal("0.1000"), Decimal("1234567890123456.0001")]
    second_values = [Decimal("0.2000"), Decimal("9876543210987654.0002")]
    first_upload, first_file, first_bronze = register_and_bronze(
        "exact-first.csv", "order_id,amount\nO001,0.1000\nO002,1234567890123456.0001\n",
    )
    version_id = first_bronze["dataset_version"]["dataset_version_id"]
    association_id = first_bronze["dataset_version_file"][0]
    questions = rules.generate_rule_suggestions(first_file)
    assert any(q["column_name"] == "amount" and q["suggested_rule_type"] == "DATA_TYPE" for q in questions)
    answers = [
        BusinessRuleAnswer(
            column_name=q["column_name"], rule_type=q["suggested_rule_type"],
            answer="DECIMAL_EXACT:P20:S4" if q["column_name"] == "amount"
            and q["suggested_rule_type"] == "DATA_TYPE" else "YES",
        )
        for q in questions
    ]
    with context.rule_mutation(association_id, user, 0, workspace_id, dataset_id):
        assert rules.submit_business_rule_answers(association_id, answers)["rule_version"] == 1
    with context.rule_mutation(association_id, user, 1, workspace_id, dataset_id):
        assert rules.finalize_business_rules(association_id)["finalized"]
    approved = get_approved_policy(version_id, 1)
    assert any(r[2:5] == ("amount", "DATA_TYPE", {
        "type": "DECIMAL", "mode": "EXACT", "precision": 20, "scale": 4,
    }) for r in approved)
    first_silver = silver_for(first_bronze, first_values)
    policy = save_policy(workspace_id, dataset_id, user, LoadPolicyRequest(
        dataset_version_id=version_id, expected_policy_version=0,
        load_strategy="APPEND", business_keys=[], schema_evolution_policy="STRICT",
    ))
    first = apply(first_upload)
    first_state, first_frame = execution.current_state(store, first, policy, exact_rules=approved)
    assert first["status"] == "SUCCESS"
    assert (first["inserted_rows"], first["current_state_rows"], first_state["row_count"]) == (2, 2, 2)
    assert first["source_dq_run_id"] == first_silver["dq_run_id"]
    assert first_state["state_id"] == first["result_state_id"] == head()["current_state_id"]
    assert first_state["status"] == "PUBLISHED"
    assert first_frame["amount"].tolist() == first_values
    assert first_frame["_datarise_upload_id"].tolist() == [first_upload] * 2
    assert first_frame["_datarise_application_id"].tolist() == [first["application_id"]] * 2
    first_manifest = json.loads(storage.objects[first_state["manifest_key"]])
    assert first_manifest["manifest_version"] == 2
    assert_decimal_artifact(first_manifest["data_key"], first_values)
    committed_head, committed_states, committed_apps = head(), states(), applications()

    second_upload, second_file, second_bronze = register_and_bronze(
        "exact-second.csv", "order_id,amount\nO003,0.2000\nO004,9876543210987654.0002\n",
    )
    assert second_upload != first_upload and second_file != first_file
    assert second_bronze["dataset_version_file"][0] != association_id
    assert second_bronze["dataset_version"]["dataset_version_id"] == version_id
    # Same-schema approval is reused by the real production approval gate;
    # submitting a new rule version would require an explicit state migration.
    second_context = context.read_processing_context(second_upload, user, workspace_id, dataset_id)
    assert second_context["rules_reused"] and second_context["rule_state"] == "FINALIZED"
    assert second_context["rule_version"] == 1 and second_context["silver_can_proceed"]
    assert get_approved_policy(version_id, 1) == approved
    second_silver = silver_for(second_bronze, second_values)
    assert second_silver["dq_run_id"] != first_silver["dq_run_id"]

    original_publish = execution._publish_metadata
    attempted = []

    def publish_then_fail(conn, *args):
        result = original_publish(conn, *args)
        # These are actual uncommitted PostgreSQL writes, not manufactured
        # return values. The separate observer still sees the first head.
        assert result["status"] == "SUCCESS"
        assert result["upload_request_id"] == second_upload
        assert conn.execute("SELECT current_state_id FROM datasets WHERE dataset_id=%s", (dataset_id,)).fetchone()[0] == result["result_state_id"]
        assert conn.execute("SELECT status FROM dataset_state_versions WHERE state_id=%s", (result["result_state_id"],)).fetchone()[0] == "PUBLISHED"
        assert conn.execute("SELECT status, result_state_id FROM delivery_applications WHERE application_id=%s", (result["application_id"],)).fetchone() == ("SUCCESS", result["result_state_id"])
        assert head() == committed_head
        attempted.append(result)
        raise RuntimeError("injected after real publication writes before commit")

    with monkeypatch.context() as failure:
        failure.setattr(execution, "_publish_metadata", publish_then_fail)
        with pytest.raises(RuntimeError, match="previous trusted state remains active") as error:
            apply(second_upload)
    assert str(error.value.__cause__) == "injected after real publication writes before commit"
    assert len(attempted) == 1
    failed_id = attempted[0]["application_id"]
    assert head() == committed_head
    assert states()[0] == committed_states[0]
    assert applications()[0] == committed_apps[0]
    failed = applications()[1]
    assert failed["application_id"] == failed_id and failed["status"] == "FAILED"
    assert failed["failure_code"] == "APPLICATION_FAILED"
    assert failed["result_state_id"] is None and failed["current_state_rows"] is None
    assert failed["inserted_rows"] is None
    failed_state = states()[1]
    assert failed_state["state_id"] == attempted[0]["result_state_id"]
    assert failed_state["status"] == "STAGED"
    assert failed_state["row_count"] is None
    assert failed_state["published_at"] is None and failed_state["validated_at"] is None
    assert failed_state["previous_state_id"] == first_state["state_id"]
    assert execution.current_state(store, first, policy, exact_rules=approved)[1].equals(first_frame)

    pinned_manifests = manifests()
    assert len(pinned_manifests) == 2
    assert all(m["manifest_version"] == 2 for m in pinned_manifests)
    pinned = pinned_manifests[1]
    assert pinned["application_id"] == failed_id
    assert pinned["silver_key"] != pinned_manifests[0]["silver_key"]
    delivery = json.loads(storage.objects[pinned["manifest_key"]])
    assert delivery["upload_request_id"] == second_upload
    assert delivery["physical_file_id"] == second_file
    assert delivery["source_dq_run_id"] == second_silver["dq_run_id"]
    assert delivery["source_silver_sha256"] == hashlib.sha256(storage.objects[second_silver["silver_path"]]).hexdigest()
    assert_decimal_artifact(pinned["silver_key"], second_values)
    old_objects = dict(storage.objects)
    processing_before = rows(
        "SELECT * FROM processing_attempts WHERE dataset_version_file_id IN (%s,%s) ORDER BY attempt_id",
        (association_id, second_bronze["dataset_version_file"][0]),
    )

    # Prove retry consumes the pinned copy without rereading mutable Silver.
    storage.forbidden_reads.add(second_silver["silver_path"])
    storage.reads.clear()
    recovered = apply(second_upload)
    assert recovered["application_id"] == failed_id
    assert recovered["status"] == "SUCCESS" and recovered["failure_code"] is None
    assert (recovered["inserted_rows"], recovered["duplicate_rows"], recovered["incremental_rejected_rows"], recovered["current_state_rows"]) == (2, 0, 0, 4)
    assert recovered["source_dq_run_id"] == second_silver["dq_run_id"]
    assert recovered["applied_rule_version"] == 1
    assert pinned["manifest_key"] in storage.reads and pinned["silver_key"] in storage.reads
    assert manifests() == pinned_manifests
    assert all(storage.objects[key] == body for key, body in old_objects.items())
    assert rows(
        "SELECT * FROM processing_attempts WHERE dataset_version_file_id IN (%s,%s) ORDER BY attempt_id",
        (association_id, second_bronze["dataset_version_file"][0]),
    ) == processing_before
    final_state, final_frame = execution.current_state(store, recovered, policy, exact_rules=approved)
    assert final_state["state_id"] == recovered["result_state_id"] == head()["current_state_id"]
    assert final_state["source_application_id"] == failed_id
    assert final_state["previous_state_id"] == first_state["state_id"]
    assert final_state["policy_id"] == policy["policy_id"]
    assert final_state["row_count"] == 4 and final_state["status"] == "PUBLISHED"
    assert final_frame["order_id"].tolist() == ["O001", "O002", "O003", "O004"]
    assert final_frame["order_id"].is_unique
    assert final_frame["amount"].tolist() == first_values + second_values
    assert final_frame["_datarise_upload_id"].tolist() == [first_upload] * 2 + [second_upload] * 2
    assert final_frame["_datarise_application_id"].tolist() == [first["application_id"]] * 2 + [failed_id] * 2
    assert final_frame["_datarise_policy_id"].tolist() == [policy["policy_id"]] * 4
    assert final_frame["_datarise_rule_version"].tolist() == [1] * 4
    final_manifest = json.loads(storage.objects[final_state["manifest_key"]])
    assert final_manifest["manifest_version"] == 2
    assert final_manifest["delivery_manifest_id"] == pinned["manifest_id"]
    assert final_manifest["previous_state_id"] == first_state["state_id"]
    assert_decimal_artifact(final_manifest["data_key"], first_values + second_values)
    assert states()[0] == committed_states[0]
    assert states()[1] == failed_state
    assert len(states()) == 3  # Failed candidate is retained, never published.
    assert [s["source_application_id"] for s in states() if s["status"] == "PUBLISHED"] == [first["application_id"], failed_id]
    assert len(applications()) == 2 and all(a["status"] == "SUCCESS" for a in applications())

    saved_head, saved_states, saved_apps = head(), states(), applications()
    saved_objects, saved_writes = dict(storage.objects), list(storage.writes)
    assert apply(second_upload) == recovered
    assert head() == saved_head and states() == saved_states and applications() == saved_apps
    assert manifests() == pinned_manifests
    assert storage.objects == saved_objects and storage.writes == saved_writes
