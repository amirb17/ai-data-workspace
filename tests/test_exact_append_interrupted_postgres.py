"""Abandoned Exact APPEND applications recover under real PostgreSQL locks.

Reuses the established synthetic exact-Silver fixture; source approval and
Bronze/Silver fidelity are covered by the genuine pipeline/rollback E2E tests.
Application transitions, candidates, constraints and publication stay real.
"""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from psycopg.rows import dict_row

from app.services import append_application_service as execution
from app.services.processing_context_service import read_processing_context
from tests.test_append_execution import apply
from tests.test_exact_append_postgres import (
    append_pipeline,
    deliveries,
    pg,
    pipeline,
    prepare_exact_test_source,
)
from tests.test_incremental_foundation import second_delivery


class WorkerInterrupted(BaseException):
    """Escape Exception handling without manually changing persisted status."""


def test_abandoned_running_exact_append_recovers_pinned_delivery(append_pipeline, monkeypatch):
    database, objects, policy, approved = prepare_exact_test_source(append_pipeline, monkeypatch)
    connect, user, workspace_id, dataset_id, *_ = database
    monkeypatch.setenv("DATARISE_EXACT_APPEND_V2_TEST_ONLY", "1")
    store = execution.IncrementalArtifacts()
    monkeypatch.setattr(execution, "IncrementalArtifacts", lambda: store)

    def snapshot():
        with connect() as conn:
            cursor = conn.cursor(row_factory=dict_row)
            head = cursor.execute(
                "SELECT current_state_id, current_gold_run_id, state_analytics_status "
                "FROM datasets WHERE dataset_id=%s", (dataset_id,),
            ).fetchone()
            apps = cursor.execute(
                "SELECT * FROM delivery_applications WHERE dataset_id=%s ORDER BY application_id",
                (dataset_id,),
            ).fetchall()
            states = cursor.execute(
                "SELECT * FROM dataset_state_versions WHERE dataset_id=%s ORDER BY state_version",
                (dataset_id,),
            ).fetchall()
            manifests = cursor.execute(
                "SELECT m.* FROM delivery_manifests m JOIN delivery_applications a "
                "ON a.application_id=m.application_id WHERE a.dataset_id=%s ORDER BY m.manifest_id",
                (dataset_id,),
            ).fetchall()
            return head, apps, states, manifests

    def assert_exact(key, expected):
        table = pq.read_table(io.BytesIO(objects[key]))
        assert table.schema.field("amount").type == pa.decimal128(20, 4)
        values = table.column("amount").to_pylist()
        assert values == expected
        assert all(value.as_tuple().exponent == -4 for value in values)

    first = apply(append_pipeline)
    baseline = snapshot()
    first_state, first_frame = execution.current_state(store, first, policy, exact_rules=approved)
    assert first["status"] == "SUCCESS"
    assert first["current_state_rows"] == first_state["row_count"] == 2
    assert baseline[0]["current_state_id"] == first["result_state_id"] == first_state["state_id"]
    assert first_state["status"] == "PUBLISHED"
    amounts = [Decimal("10.0000"), Decimal("20.0000")]
    first_manifest = json.loads(objects[first_state["manifest_key"]])
    assert first_manifest["manifest_version"] == 2
    assert_exact(first_manifest["data_key"], amounts)

    # An explicit unkeyed APPEND permits a separate logical delivery of the
    # same physical source. Recovery must append this arrival exactly once.
    second_upload = second_delivery(append_pipeline[0])
    original_validate = store.validate_state
    interrupted = []

    def validate_then_interrupt(key, checksum, application, *args, **kwargs):
        verified = original_validate(key, checksum, application, *args, **kwargs)
        if application["upload_request_id"] == second_upload:
            current = snapshot()
            assert current[0] == baseline[0]
            assert current[1][-1]["status"] == "RUNNING"
            assert current[2][-1]["status"] == "STAGED"
            assert current[2][-1]["manifest_key"] == key
            assert current[3][-1]["manifest_version"] == 2
            assert len(verified) == 4
            # Separate thread = separate ContextVars and PostgreSQL session.
            # The real in-flight dataset lock rejects takeover immediately;
            # no sleep, timestamp alteration or lease-expiry guess is needed.
            with ThreadPoolExecutor(max_workers=1) as pool:
                contender = pool.submit(apply, append_pipeline, second_upload)
                with pytest.raises(RuntimeError, match="already active"):
                    contender.result()
            assert snapshot() == current
            interrupted.append(current)
            raise WorkerInterrupted("worker ended after staging exact candidate")
        return verified

    with monkeypatch.context() as interruption:
        interruption.setattr(store, "validate_state", validate_then_interrupt)
        with pytest.raises(WorkerInterrupted, match="after staging exact candidate"):
            apply(append_pipeline, second_upload)
    assert len(interrupted) == 1
    abandoned = snapshot()
    assert abandoned == interrupted[0]
    head, apps, states, manifests = abandoned
    running, staged, pinned = apps[-1], states[-1], manifests[-1]
    assert head == baseline[0]
    assert apps[0] == baseline[1][0] and states[0] == baseline[2][0]
    assert len(apps) == len(states) == len(manifests) == 2
    assert running["status"] == "RUNNING" and running["started_at"] is not None
    assert running["completed_at"] is None and running["failure_code"] is None
    assert running["result_state_id"] is None and running["inserted_rows"] is None
    assert staged["status"] == "STAGED" and staged["published_at"] is None
    assert staged["previous_state_id"] == first["result_state_id"]
    assert staged["source_application_id"] == pinned["application_id"] == running["application_id"]
    assert execution.current_state(store, first, policy, exact_rules=approved)[1].equals(first_frame)

    with pytest.raises(PermissionError):
        execution.apply_incremental(
            workspace_id, dataset_id, {**user, "owner_key": "dev:other"}, second_upload,
        )
    assert snapshot() == abandoned
    # RUNNING is projected as processing, not a timed-out lease. Recovery is
    # an explicit normal apply call once the session advisory locks are free.
    context = read_processing_context(second_upload, user, workspace_id, dataset_id)
    assert context["status"] == "DATASET_UPDATE_PROCESSING"
    assert context["can_continue"] is False

    delivery = json.loads(objects[pinned["manifest_key"]])
    assert delivery["manifest_version"] == 2
    assert delivery["upload_request_id"] == second_upload
    assert delivery["source_dq_run_id"] == running["source_dq_run_id"]
    assert_exact(pinned["silver_key"], amounts)
    with connect() as conn:
        source_key = conn.execute(
            "SELECT silver_path FROM data_quality_runs WHERE dq_run_id=%s",
            (running["source_dq_run_id"],),
        ).fetchone()[0]
        attempts_before = conn.execute(
            "SELECT * FROM processing_attempts WHERE dataset_version_file_id=%s ORDER BY attempt_id",
            (running["dataset_version_file_id"],),
        ).fetchall()
    objects[source_key] = b"upstream Silver changed after the worker was interrupted"
    objects_before_retry = dict(objects)
    reads = []
    original_read = store.read

    def read_pinned_only(key, *args, **kwargs):
        assert key != source_key, "Recovery reread mutable upstream Silver"
        reads.append(key)
        return original_read(key, *args, **kwargs)

    with monkeypatch.context() as recovery:
        recovery.setattr(store, "read", read_pinned_only)
        recovered = apply(append_pipeline, second_upload)
    assert recovered["status"] == "SUCCESS"
    assert recovered["application_id"] == running["application_id"]
    assert recovered["source_dq_run_id"] == running["source_dq_run_id"]
    assert recovered["policy_id"] == running["policy_id"] == policy["policy_id"]
    assert recovered["applied_rule_version"] == running["applied_rule_version"] == 1
    assert recovered["failure_code"] is None and recovered["completed_at"] is not None
    assert (recovered["inserted_rows"], recovered["duplicate_rows"],
            recovered["incremental_rejected_rows"], recovered["current_state_rows"]) == (2, 0, 0, 4)
    assert pinned["manifest_key"] in reads and pinned["silver_key"] in reads
    published = snapshot()
    assert published[0]["current_state_id"] == recovered["result_state_id"]
    assert published[1][0] == baseline[1][0] and len(published[1]) == 2
    assert all(a["status"] == "SUCCESS" for a in published[1])
    assert published[2][0] == baseline[2][0] and published[2][1] == staged
    assert len(published[2]) == 3  # Abandoned STAGED candidate is retained.
    assert [s["source_application_id"] for s in published[2] if s["status"] == "PUBLISHED"] == [
        first["application_id"], recovered["application_id"],
    ]
    assert published[3] == manifests
    assert all(objects[key] == body for key, body in objects_before_retry.items())
    state, frame = execution.current_state(store, recovered, policy, exact_rules=approved)
    assert state["state_id"] == recovered["result_state_id"]
    assert state["previous_state_id"] == first_state["state_id"]
    assert state["source_application_id"] == recovered["application_id"]
    assert state["row_count"] == 4 and state["status"] == "PUBLISHED"
    assert frame["amount"].tolist() == amounts * 2
    assert frame["id"].tolist() == first_frame["id"].tolist() * 2
    assert frame["_datarise_upload_id"].tolist() == [first["upload_request_id"]] * 2 + [second_upload] * 2
    assert frame["_datarise_application_id"].tolist() == [first["application_id"]] * 2 + [recovered["application_id"]] * 2
    assert frame["_datarise_policy_id"].tolist() == [policy["policy_id"]] * 4
    assert frame["_datarise_rule_version"].tolist() == [1] * 4
    manifest = json.loads(objects[state["manifest_key"]])
    assert manifest["manifest_version"] == 2
    assert manifest["delivery_manifest_id"] == pinned["manifest_id"]
    assert_exact(manifest["data_key"], amounts * 2)
    with connect() as conn:
        assert conn.execute(
            "SELECT * FROM processing_attempts WHERE dataset_version_file_id=%s ORDER BY attempt_id",
            (running["dataset_version_file_id"],),
        ).fetchall() == attempts_before

    final_objects = dict(objects)
    assert apply(append_pipeline, second_upload) == recovered
    assert snapshot() == published and objects == final_objects
