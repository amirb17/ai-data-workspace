"""Genuine approved Exact Decimal Bronze -> Silver -> APPEND test.

Uses isolated PostgreSQL and an in-memory S3 implementation.
No synthetic Silver replacement or approved-rule resolver override.
"""
from pathlib import Path

from app.services.exact_gold_publish_service import (
    publish_exact_gold_candidate,
)
import hashlib
import io
import json
from decimal import Decimal
from psycopg.rows import dict_row
import psycopg
from psycopg.types.json import Jsonb
from app.services.exact_gold_approval_service import (
    approve_exact_gold_avg_policy,
)
from app.services.exact_gold_avg_policy import (
    approved_rules_fingerprint,
    read_pinned_avg_policy,
)
from app.services.exact_gold_state_reader import (
    read_pinned_exact_state,
)
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.identity import get_current_user
from app.services import exact_gold_approval_service as approval_service
from tests.http_client import enable_csrf
from app.config import S3_BUCKET_NAME
from app.processing import bronze_processor, silver_processor
from app.schemas.business_rules import BusinessRuleAnswer
from app.services import business_rule_service as rules
from app.services import processing_context_service as context
from app.services import processing_service as stages
from app.services import rule_reuse_service as reuse
from app.services import append_application_service as execution
from app.services.rule_suggestion_service import (
    generate_rule_suggestions,
)
from app.services.incremental_policy_service import save_policy
from app.schemas.incremental import LoadPolicyRequest
from app.db.processing_repository import get_approved_policy
from app.storage.incremental_artifacts import IncrementalArtifacts

from tests.test_rule_approval_postgres import pg
from app.services.exact_gold_candidate_service import (
    prepare_exact_gold_candidate,
)
from app.storage.exact_cumulative_gold_artifacts import (
    ExactCumulativeGoldArtifacts,
)
from app.services.exact_gold_candidate_service import (
    prepare_exact_gold_candidate,
    pin_exact_gold_candidate,
)
CSV = (
    "order_id,amount\n"
    "O001,0.1000\n"
    "O002,1234567890123456.0001\n"
)

EXPECTED = [
    Decimal("0.1000"),
    Decimal("1234567890123456.0001"),
]


class MemoryS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        assert Bucket == S3_BUCKET_NAME
        return {
            "Body": io.BytesIO(self.objects[Key])
        }

    def put_object(self, Bucket, Key, Body, **kwargs):
        assert Bucket == S3_BUCKET_NAME

        if kwargs.get("IfNoneMatch") == "*":
            if Key in self.objects:
                raise AssertionError(
                    "Immutable S3 object overwritten"
                )

        self.objects[Key] = bytes(Body)
        return {}


def test_real_approved_exact_pipeline_e2e(pg, monkeypatch):
    connect, user, workspace_id, dataset_id, *_ = pg

    storage = MemoryS3()
    raw_bytes = CSV.encode("utf-8")
    raw_key = "raw/exact-e2e.csv"

    storage.objects[raw_key] = raw_bytes

    # Use real Bronze, real Silver and real APPEND logic.
    # Only external S3 storage is replaced.
    monkeypatch.setattr(
        bronze_processor,
        "get_s3_client",
        lambda: storage,
    )

    monkeypatch.setattr(
        silver_processor,
        "s3_client",
        storage,
    )

    monkeypatch.setattr(
        execution,
        "IncrementalArtifacts",
        lambda: IncrementalArtifacts(storage),
    )

    # The imported pg fixture replaces suggestions with a
    # simple NOT_NULL test rule. Restore the actual generator
    # so approval follows the genuine business-rule workflow.
    monkeypatch.setattr(
        rules,
        "generate_rule_suggestions",
        generate_rule_suggestions,
    )

    monkeypatch.setattr(
        reuse,
        "generate_rule_suggestions",
        generate_rule_suggestions,
    )

    monkeypatch.setenv(
        "DATARISE_EXACT_APPEND_V2_TEST_ONLY",
        "1",
    )

    # ----------------------------------------------------
    # 1. Register original CSV in isolated PostgreSQL.
    # ----------------------------------------------------
    with connect() as conn:
        file_id = conn.execute(
            """
            INSERT INTO physical_files(
                file_name,
                file_size,
                file_hash,
                storage_path,
                status
            )
            VALUES (%s,%s,%s,%s,'UPLOADED')
            RETURNING file_id
            """,
            (
                "exact-e2e.csv",
                len(raw_bytes),
                hashlib.sha256(raw_bytes).hexdigest(),
                f"s3://{S3_BUCKET_NAME}/{raw_key}",
            ),
        ).fetchone()[0]

        upload_id = conn.execute(
            """
            INSERT INTO upload_requests(
                user_id,
                workspace_id,
                dataset_id,
                file_id,
                status
            )
            VALUES (%s,%s,%s,%s,'UPLOADED')
            RETURNING upload_id
            """,
            (
                user["user_id"],
                workspace_id,
                dataset_id,
                file_id,
            ),
        ).fetchone()[0]

    # ----------------------------------------------------
    # 2. Execute real Bronze processing.
    # ----------------------------------------------------
    bronze_result = stages.start_processing(upload_id)

    version_id = bronze_result["dataset_version"][
        "dataset_version_id"
    ]

    association_id = bronze_result[
        "dataset_version_file"
    ][0]

    assert (
        f"bronze/file_id={file_id}/data.parquet"
        in storage.objects
    )

    assert (
        f"bronze/file_id={file_id}/raw_text.parquet"
        in storage.objects
    )

    # ----------------------------------------------------
    # 3. Submit a genuine approved Exact Decimal rule.
    # ----------------------------------------------------
    questions = rules.generate_rule_suggestions(file_id)

    assert any(
        q["column_name"] == "amount"
        and q["suggested_rule_type"] == "DATA_TYPE"
        for q in questions
    )

    answers = []

    for question in questions:
        is_exact = (
            question["column_name"] == "amount"
            and question["suggested_rule_type"] == "DATA_TYPE"
        )

        answers.append(
            BusinessRuleAnswer(
                column_name=question["column_name"],
                rule_type=question["suggested_rule_type"],
                answer=(
                    "DECIMAL_EXACT:P20:S4"
                    if is_exact
                    else "YES"
                ),
            )
        )

    with context.rule_mutation(
        association_id,
        user,
        0,
        workspace_id,
        dataset_id,
    ):
        saved = rules.submit_business_rule_answers(
            association_id,
            answers,
        )

    assert saved["rule_version"] == 1

    with context.rule_mutation(
        association_id,
        user,
        1,
        workspace_id,
        dataset_id,
    ):
        finalized = rules.finalize_business_rules(
            association_id
        )

    assert finalized["finalized"]

    approved = get_approved_policy(version_id, 1)

    assert any(
        rule[2] == "amount"
        and rule[3] == "DATA_TYPE"
        and rule[4] == {
            "type": "DECIMAL",
            "mode": "EXACT",
            "precision": 20,
            "scale": 4,
        }
        for rule in approved
    )

    # ----------------------------------------------------
    # 4. Run genuine Silver processing.
    # ----------------------------------------------------
    silver = stages.run_silver_processing(
        association_id,
        S3_BUCKET_NAME,
    )

    assert silver["status"] == "SUCCESS"
    assert silver["valid_rows"] == 2
    assert silver["rejected_rows"] == 0

    silver_key = silver["silver_path"]

    # Verify physical Parquet type, not just values.
    physical_schema = pq.read_schema(
        io.BytesIO(storage.objects[silver_key])
    )

    assert physical_schema.field("amount").type == (
        pa.decimal128(20, 4)
    )

    with connect() as conn:
        stored_sha = conn.execute(
            """
            SELECT silver_sha256
            FROM data_quality_runs
            WHERE dq_run_id = %s
            """,
            (silver["dq_run_id"],),
        ).fetchone()[0]

    assert stored_sha == hashlib.sha256(
        storage.objects[silver_key]
    ).hexdigest()

    # ----------------------------------------------------
    # 5. Create the actual APPEND policy.
    # ----------------------------------------------------
    policy = save_policy(
        workspace_id,
        dataset_id,
        user,
        LoadPolicyRequest(
            dataset_version_id=version_id,
            expected_policy_version=0,
            load_strategy="APPEND",
            business_keys=[],
            schema_evolution_policy="STRICT",
        ),
    )

    # ----------------------------------------------------
    # 6. Publish using the real APPEND coordinator.
    # ----------------------------------------------------
    application = execution.apply_incremental(
        workspace_id,
        dataset_id,
        user,
        upload_id,
    )

    assert application["status"] == "SUCCESS"
    assert application["current_state_rows"] == 2
    assert application["inserted_rows"] == 2

    # ----------------------------------------------------
    # 7. Read the published V2 state.
    # ----------------------------------------------------
    store = IncrementalArtifacts(storage)

    state, frame = execution.current_state(
        store,
        application,
        policy,
        exact_rules=approved,
    )

    assert state["status"] == "PUBLISHED"
    assert frame["amount"].tolist() == EXPECTED

    state_manifest = json.loads(
        storage.objects[state["manifest_key"]]
    )

    assert state_manifest["manifest_version"] == 2
    assert (
        state_manifest["exact_descriptor"][
            "manifest_version"
        ] == 2
    )

    # ----------------------------------------------------
    # 8. A successful retry changes nothing.
    # ----------------------------------------------------
    before_keys = set(storage.objects)

    repeated = execution.apply_incremental(
        workspace_id,
        dataset_id,
        user,
        upload_id,
    )

    assert repeated == application
    assert set(storage.objects) == before_keys
        # ----------------------------------------------------
    # 9. Read the real published V2 state through the
    #    isolated Exact Gold trusted-state reader.
    # ----------------------------------------------------
    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY",
        "1",
    )

    with connect() as conn:
        pinned_application = conn.cursor(
            row_factory=dict_row
        ).execute(
            """
            SELECT *
            FROM delivery_applications
            WHERE application_id = %s
            """,
            (state["source_application_id"],),
        ).fetchone()

    assert pinned_application is not None

    trusted_state, trusted_frame, pinned_rules = (
        read_pinned_exact_state(
            store,
            state,
            pinned_application,
            policy,
        )
    )

    assert trusted_state["state_id"] == state["state_id"]

    assert trusted_frame["amount"].tolist() == [
        Decimal("0.1000"),
        Decimal("1234567890123456.0001"),
    ]

    assert pinned_rules == approved

    # ----------------------------------------------------
    # 10. Ensure the test-only read gate remains enforced.
    # ----------------------------------------------------
    monkeypatch.delenv(
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY"
    )

    with pytest.raises(
        ValueError,
        match="restricted to isolated tests",
    ):
        read_pinned_exact_state(
            store,
            state,
            pinned_application,
            policy,
        )
        # ----------------------------------------------------
    # 11. Test-only insertion of a pinned AVG policy.
    # This is NOT the production approval workflow.
    # ----------------------------------------------------
    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY",
        "1",
    )

    rules_hash = approved_rules_fingerprint(approved)

    with connect() as conn:
        approval_id = conn.execute(
            """
            INSERT INTO exact_gold_avg_policies(
                source_state_id,
                source_application_id,
                dataset_id,
                dataset_version_id,
                source_policy_id,
                applied_rule_version,
                avg_scales,
                approved_rules_sha256,
                approved_by
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING approval_id
            """,
            (
                state["state_id"],
                pinned_application["application_id"],
                state["dataset_id"],
                state["dataset_version_id"],
                state["policy_id"],
                pinned_application["applied_rule_version"],
                Jsonb({"amount": 5}),
                rules_hash,
                user["user_id"],
            ),
        ).fetchone()[0]

    pinned_policy = read_pinned_avg_policy(
        state["state_id"]
    )

    assert pinned_policy["approval_id"] == approval_id
    assert pinned_policy["avg_scales"] == {"amount": 5}
    assert pinned_policy["approved_rules"] == approved

    # Successful approvals cannot be modified.
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="immutable",
    ):
        with connect() as conn:
            conn.execute(
                """
                UPDATE exact_gold_avg_policies
                SET avg_scales = %s
                WHERE approval_id = %s
                """,
                (
                    Jsonb({"amount": 6}),
                    approval_id,
                ),
            )

    # The same source state cannot be approved twice.
    with pytest.raises(psycopg.errors.UniqueViolation):
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO exact_gold_avg_policies(
                    source_state_id,
                    source_application_id,
                    dataset_id,
                    dataset_version_id,
                    source_policy_id,
                    applied_rule_version,
                    avg_scales,
                    approved_rules_sha256,
                    approved_by
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    state["state_id"],
                    pinned_application["application_id"],
                    state["dataset_id"],
                    state["dataset_version_id"],
                    state["policy_id"],
                    pinned_application["applied_rule_version"],
                    Jsonb({"amount": 6}),
                    rules_hash,
                    user["user_id"],
                ),
            )
        # ----------------------------------------------------
    # 12. V2 Gold run can be prepared only with its
    #     pinned AVG policy.
    # ----------------------------------------------------
    with connect() as conn:
        gold_run_id = conn.execute(
            """
            INSERT INTO dataset_gold_runs (
                dataset_id,
                source_state_id,
                dataset_version_id,
                policy_id,
                source_sha256,
                build_version,
                exact_gold_approval_id,
                status
            )
            VALUES (%s,%s,%s,%s,%s,2,%s,'REFRESHING')
            RETURNING gold_run_id
            """,
            (
                state["dataset_id"],
                state["state_id"],
                state["dataset_version_id"],
                state["policy_id"],
                state["manifest_sha256"],
                approval_id,
            ),
        ).fetchone()[0]

    # A V2 success cannot be recorded while publication
    # remains deliberately disabled.
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="publication is not enabled",
    ):
        with connect() as conn:
            conn.execute(
                """
                UPDATE dataset_gold_runs
                SET status = 'SUCCESS'
                WHERE gold_run_id = %s
                """,
                (gold_run_id,),
            )

    # The rejected update must not publish anything.
    with connect() as conn:
        recorded = conn.execute(
            """
            SELECT status, build_version, exact_gold_approval_id
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
            """,
            (gold_run_id,),
        ).fetchone()

        assert recorded == (
            "REFRESHING",
            2,
            approval_id,
        )

        head = conn.execute(
            """
            SELECT current_gold_run_id
            FROM datasets
            WHERE dataset_id = %s
            """,
            (state["dataset_id"],),
        ).fetchone()

        assert head[0] != gold_run_id
    # ----------------------------------------------------
    # 13. HTTP approval boundary:
    # authentication, CSRF, ownership and validation.
    # ----------------------------------------------------

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_MODE", "session")
    monkeypatch.setenv("AUTH_SECRET", "test-only-secret-" * 3)
    monkeypatch.setenv(
        "AUTH_FRONTEND_URL",
        "http://localhost:5173",
    )
    monkeypatch.setenv(
        "AUTH_BACKEND_URL",
        "http://localhost:8000",
    )
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY",
        "1",
    )
    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY",
        "1",
    )

    # The endpoint normally constructs an S3-backed store.
    # Keep this isolated test on the existing MemoryS3 store.
    monkeypatch.setattr(
        approval_service,
        "IncrementalArtifacts",
        lambda: store,
    )

    url = (
        f"/workspaces/{workspace_id}"
        f"/datasets/{dataset_id}"
        "/analytics/gold/avg-policy/approve"
    )

    payload = {
        "source_state_id": state["state_id"],
        "expected_rules_sha256": rules_hash,
        "avg_scales": {"amount": 5},
        "confirm_approval": True,
    }

    previous_override = app.dependency_overrides.get(
        get_current_user
    )

    app.dependency_overrides[get_current_user] = (
        lambda: user
    )

    try:
        # 1. Missing CSRF token must be rejected.
        no_csrf_client = TestClient(app)

        response = no_csrf_client.post(
            url,
            json=payload,
        )

        assert response.status_code == 403

        # 2. Valid CSRF + authenticated owner succeeds.
        client = enable_csrf(TestClient(app))

        response = client.post(
            url,
            json=payload,
        )

        assert response.status_code == 200, response.text
        assert response.json()["approval_id"] == approval_id
        assert response.json()["already_approved"] is True

        # 3. Explicit confirmation is mandatory.
        unconfirmed = {
            **payload,
            "confirm_approval": False,
        }

        response = client.post(
            url,
            json=unconfirmed,
        )

        assert response.status_code == 422

        # 4. Floating-point AVG scales are invalid.
        invalid_scale = {
            **payload,
            "avg_scales": {"amount": 5.0},
        }

        response = client.post(
            url,
            json=invalid_scale,
        )

        assert response.status_code == 422

        # 5. Conflicting approved scale is rejected.
        changed_scale = {
            **payload,
            "avg_scales": {"amount": 6},
        }

        response = client.post(
            url,
            json=changed_scale,
        )

        assert response.status_code == 400

        # 6. Stale fingerprint is rejected.
        stale_rules = {
            **payload,
            "expected_rules_sha256": "0" * 64,
        }

        response = client.post(
            url,
            json=stale_rules,
        )

        assert response.status_code == 400

        # 7. Wrong dataset owner cannot approve.
        app.dependency_overrides[get_current_user] = (
            lambda: {
                **user,
                "owner_key": "another-owner",
            }
        )

        response = client.post(
            url,
            json=payload,
        )

        assert response.status_code == 403

        # Restore the valid owner.
        app.dependency_overrides[get_current_user] = (
            lambda: user
        )

        # 8. Feature gate disabled: fail closed.
        monkeypatch.delenv(
            "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY"
        )

        response = client.post(
            url,
            json=payload,
        )

        assert response.status_code == 409

        monkeypatch.setenv(
            "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY",
            "1",
        )

        # 9. CSRF alone does not authenticate a user.
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

        response = client.post(
            url,
            json=payload,
        )

        assert response.status_code == 401

    finally:
        if previous_override is None:
            app.dependency_overrides.pop(
                get_current_user,
                None,
            )
        else:
            app.dependency_overrides[
                get_current_user
            ] = previous_override

    # 10. API calls must not create duplicate approvals.
    with connect() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM exact_gold_avg_policies
            WHERE source_state_id = %s
            """,
            (state["state_id"],),
        ).fetchone()[0]

        assert count == 1

        # No V2 Gold publication should have occurred.
        published = conn.execute(
            """
            SELECT COUNT(*)
            FROM dataset_gold_runs
            WHERE source_state_id = %s
              AND build_version = 2
              AND status = 'SUCCESS'
            """,
            (state["state_id"],),
        ).fetchone()[0]

        assert published == 0
        # ----------------------------------------------------
    # 14. Prepare real Exact Decimal V2 Gold artifacts.
    # Publication must remain disabled.
    # ----------------------------------------------------

    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY",
        "1",
    )
    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY",
        "1",
    )

    gold_store = ExactCumulativeGoldArtifacts(storage)

    candidate = prepare_exact_gold_candidate(
        workspace_id,
        dataset_id,
        user,
        state["state_id"],
        store=gold_store,
    )

    assert candidate["status"] == (
        "VALIDATED_NOT_PUBLISHED"
    )

    assert candidate["published"] is False
    assert candidate["gold_run_id"] == gold_run_id
    assert candidate["approval_id"] == approval_id
    assert candidate["row_count"] == 2

    assert candidate["manifest_key"] in storage.objects

    # The mart must have an explicit Decimal AVG contract.
    assert any(
        "amount_mean"
        in artifact["exact_parquet"]["exact_columns"]
        for artifact in candidate["catalog"]
        if artifact["artifact_type"] == "MART"
    )

    # Revalidate stored bytes independently.
    with connect() as conn:
        pinned_run = conn.cursor(
            row_factory=dict_row
        ).execute(
            """
            SELECT *
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
            """,
            (gold_run_id,),
        ).fetchone()

    # Use the pinned PostgreSQL policy as the source of truth.
    with connect() as conn:
        pinned_policy = conn.cursor(
            row_factory=dict_row
        ).execute(
            """
            SELECT *
            FROM dataset_load_policies
            WHERE policy_id = %s
            """,
            (pinned_run["policy_id"],),
        ).fetchone()

    assert pinned_policy is not None

    expected_columns = [
        column["name"]
        for column in pinned_policy["schema_columns"]
    ]

    validated_catalog = gold_store.validate_exact(
        pinned_run,
        candidate["manifest_key"],
        candidate["manifest_sha256"],
        state["row_count"],
        expected_columns,
        approved,
        {"amount": 5},
    )

    assert validated_catalog == candidate["catalog"]

    # Critically: the coordinator did not publish Gold.
    with connect() as conn:
        status = conn.execute(
            """
            SELECT status, manifest_key
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
            """,
            (gold_run_id,),
        ).fetchone()

        assert status == ("REFRESHING", None)

        head = conn.execute(
            """
            SELECT current_gold_run_id
            FROM datasets
            WHERE dataset_id = %s
            """,
            (dataset_id,),
        ).fetchone()[0]

        assert head != gold_run_id

    # Disabling the feature gate must block preparation
    # before any additional objects are created.
    before_keys = set(storage.objects)

    monkeypatch.delenv(
        "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY"
    )

    with pytest.raises(
        RuntimeError,
        match="restricted to isolated tests",
    ):
        prepare_exact_gold_candidate(
            workspace_id,
            dataset_id,
            user,
            state["state_id"],
            store=gold_store,
        )

    assert set(storage.objects) == before_keys
        # ----------------------------------------------------
    # 15. Durable Gold V2 candidate pinning.
    # ----------------------------------------------------

    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY", "1"
    )
    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY", "1"
    )

    # Corrupted storage must be rejected before any DB pin.
    base_key = candidate["catalog"][0]["storage_path"]
    original_body = storage.objects[base_key]

    try:
        storage.objects[base_key] = b"corrupted"

        with pytest.raises(ValueError, match="checksum"):
            pin_exact_gold_candidate(
                workspace_id,
                dataset_id,
                user,
                state["state_id"],
                candidate,
                store=gold_store,
            )
    finally:
        storage.objects[base_key] = original_body

    with connect() as conn:
        before = conn.execute(
            """
            SELECT manifest_key
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
            """,
            (gold_run_id,),
        ).fetchone()[0]

    assert before is None

    # First valid pin.
    pinned = pin_exact_gold_candidate(
        workspace_id,
        dataset_id,
        user,
        state["state_id"],
        candidate,
        store=gold_store,
    )

    assert pinned["status"] == "PINNED_NOT_PUBLISHED"
    assert pinned["reused"] is False
    assert pinned["published"] is False

    # Retrying the same candidate must be idempotent.
    repeated_pin = pin_exact_gold_candidate(
        workspace_id,
        dataset_id,
        user,
        state["state_id"],
        candidate,
        store=gold_store,
    )

    assert repeated_pin["reused"] is True
    assert repeated_pin["gold_run_id"] == gold_run_id

    with connect() as conn:
        row = conn.cursor(
            row_factory=dict_row
        ).execute(
            """
            SELECT
                status,
                manifest_key,
                manifest_sha256,
                catalog,
                row_count
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
            """,
            (gold_run_id,),
        ).fetchone()

        assert row["status"] == "REFRESHING"
        assert row["manifest_key"] == candidate["manifest_key"]
        assert row["manifest_sha256"] == (
            candidate["manifest_sha256"]
        )
        assert row["catalog"] == candidate["catalog"]
        assert row["row_count"] == state["row_count"]

    # Database immutability must hold independently of Python.
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="immutable",
    ):
        with connect() as conn:
            conn.execute(
                """
                UPDATE dataset_gold_runs
                SET manifest_key = manifest_key || '-changed'
                WHERE gold_run_id = %s
                """,
                (gold_run_id,),
            )

    # A valid pin is not a published run.
    with connect() as conn:
        assert conn.execute(
            """
            SELECT status
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
            """,
            (gold_run_id,),
        ).fetchone()[0] == "REFRESHING"

        assert conn.execute(
            """
            SELECT current_gold_run_id
            FROM datasets
            WHERE dataset_id = %s
            """,
            (dataset_id,),
        ).fetchone()[0] != gold_run_id
        # ----------------------------------------------------
    # 16. Legacy V1 Gold refresh rejects V2 before
    #     creating or modifying any Gold metadata.
    # ----------------------------------------------------

    from app.services import (
        dataset_analytics_service as legacy_gold
    )

    monkeypatch.setattr(
        legacy_gold,
        "CumulativeGoldArtifacts",
        lambda: gold_store,
    )

    with connect() as conn:
        before_v1_runs = conn.execute(
            """
            SELECT COUNT(*)
            FROM dataset_gold_runs
            WHERE dataset_id = %s
              AND source_state_id = %s
              AND build_version = 1
            """,
            (
                dataset_id,
                state["state_id"],
            ),
        ).fetchone()[0]

        before_dataset = conn.execute(
            """
            SELECT
                current_gold_run_id,
                state_analytics_status
            FROM datasets
            WHERE dataset_id = %s
            """,
            (dataset_id,),
        ).fetchone()

    with pytest.raises(
        RuntimeError,
        match="Exact Gold V2",
    ):
        legacy_gold.refresh_analytics(
            workspace_id,
            dataset_id,
            user,
        )

    with connect() as conn:
        after_v1_runs = conn.execute(
            """
            SELECT COUNT(*)
            FROM dataset_gold_runs
            WHERE dataset_id = %s
              AND source_state_id = %s
              AND build_version = 1
            """,
            (
                dataset_id,
                state["state_id"],
            ),
        ).fetchone()[0]

        after_dataset = conn.execute(
            """
            SELECT
                current_gold_run_id,
                state_analytics_status
            FROM datasets
            WHERE dataset_id = %s
            """,
            (dataset_id,),
        ).fetchone()

    assert after_v1_runs == before_v1_runs
    assert after_dataset == before_dataset
        # ----------------------------------------------------
    # 17. Test-only transactional V2 Gold publication.
    # ----------------------------------------------------

    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_PUBLISH_TEST_ONLY", "1"
    )
    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY", "1"
    )
    monkeypatch.setenv(
        "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY", "1"
    )

    # Read the actual tested migration so its other
    # source-ownership checks remain unchanged.
    sql_source = Path(
        "migrations/0039_exact_gold_run_guard.sql"
    ).read_text(encoding="utf-8")

    start = sql_source.index(
        "CREATE FUNCTION guard_exact_gold_v2_run()"
    )

    end = sql_source.index(
        "CREATE TRIGGER dataset_gold_v2_run_guard",
        start,
    )

    test_guard = sql_source[start:end].replace(
        "CREATE FUNCTION",
        "CREATE OR REPLACE FUNCTION",
        1,
    )

    original_condition = (
        "IF NEW.status = 'SUCCESS' THEN"
    )

    assert test_guard.count(original_condition) == 1

    test_guard = test_guard.replace(
        original_condition,
        """
        IF NEW.status = 'SUCCESS' AND NOT (
            current_schema()
                ~ '^test_phase5a_[0-9a-f]{32}$'
           AND COALESCE(
                current_setting(
                    'datarise.exact_gold_v2_test_publish',
                    true
                ),
                ''
            ) = '1'
        ) THEN
        """,
        1,
    )

    # Only alters the throwaway PostgreSQL test schema.
    with connect() as conn:
        conn.execute(test_guard)

    # Direct SQL remains blocked without the
    # transaction-local authorization setting.
    with pytest.raises(
        psycopg.errors.RaiseException,
        match="publication is not enabled",
    ):
        with connect() as conn:
            conn.execute(
                """
                UPDATE dataset_gold_runs
                SET status = 'SUCCESS'
                WHERE gold_run_id = %s
                """,
                (gold_run_id,),
            )

    # Corrupted Gold bytes cannot be published.
    base_key = candidate["catalog"][0]["storage_path"]
    original_body = storage.objects[base_key]

    try:
        storage.objects[base_key] = b"corrupted"

        with pytest.raises(ValueError, match="checksum"):
            publish_exact_gold_candidate(
                workspace_id,
                dataset_id,
                user,
                state["state_id"],
                store=gold_store,
            )
    finally:
        storage.objects[base_key] = original_body

    with connect() as conn:
        assert conn.execute(
            """
            SELECT status
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
            """,
            (gold_run_id,),
        ).fetchone()[0] == "REFRESHING"

    before_keys = set(storage.objects)

    # Publish the verified, pinned candidate.
    published = publish_exact_gold_candidate(
        workspace_id,
        dataset_id,
        user,
        state["state_id"],
        store=gold_store,
    )

    assert published["status"] == "PUBLISHED"
    assert published["published"] is True
    assert published["reused"] is False
    assert published["gold_run_id"] == gold_run_id

    with connect() as conn:
        run_result = conn.execute(
            """
            SELECT status, build_version, manifest_key
            FROM dataset_gold_runs
            WHERE gold_run_id = %s
            """,
            (gold_run_id,),
        ).fetchone()

        assert run_result == (
            "SUCCESS",
            2,
            candidate["manifest_key"],
        )

        dataset_result = conn.execute(
            """
            SELECT
                current_state_id,
                current_gold_run_id,
                state_analytics_status
            FROM datasets
            WHERE dataset_id = %s
            """,
            (dataset_id,),
        ).fetchone()

        assert dataset_result == (
            state["state_id"],
            gold_run_id,
            "FRESH",
        )

    # A repeated publication performs no additional
    # S3 writes and does not create another Gold run.
    repeated = publish_exact_gold_candidate(
        workspace_id,
        dataset_id,
        user,
        state["state_id"],
        store=gold_store,
    )

    assert repeated["reused"] is True
    assert repeated["gold_run_id"] == gold_run_id
    assert set(storage.objects) == before_keys

    with connect() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM dataset_gold_runs
            WHERE dataset_id = %s
              AND source_state_id = %s
              AND build_version = 2
            """,
            (dataset_id, state["state_id"]),
        ).fetchone()[0]

        assert count == 1