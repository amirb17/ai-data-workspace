"""Exact APPEND rebuilds abandoned work against the latest committed head.

Uses the established synthetic exact-Silver fixture and real PostgreSQL
applications, advisory locks, immutable candidates and publication transactions.
"""

import io
import json
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from psycopg.rows import dict_row

from app.services import append_application_service as execution
from tests.test_append_execution import apply
from tests.test_exact_append_interrupted_postgres import WorkerInterrupted
from tests.test_exact_append_postgres import (
    append_pipeline,
    deliveries,
    pg,
    pipeline,
    prepare_exact_test_source,
)
from tests.test_incremental_foundation import second_delivery


def test_abandoned_exact_append_rebuilds_after_another_delivery_advances_head(
    append_pipeline, monkeypatch,
):
    database, objects, policy, approved = prepare_exact_test_source(append_pipeline, monkeypatch)
    connect = database[0]
    dataset_id = database[3]
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
            return {
                "head": head,
                "apps": {a["application_id"]: a for a in apps},
                "states": {s["state_id"]: s for s in states},
                "deliveries": {m["application_id"]: m for m in manifests},
            }

    amounts = [Decimal("10.0000"), Decimal("20.0000")]

    def read_current(application, repetitions):
        state, frame = execution.current_state(store, application, policy, exact_rules=approved)
        manifest = json.loads(objects[state["manifest_key"]])
        assert state["status"] == "PUBLISHED" and state["row_count"] == repetitions * 2
        assert manifest["manifest_version"] == 2
        table = pq.read_table(io.BytesIO(objects[manifest["data_key"]]))
        assert table.schema.field("amount").type == pa.decimal128(20, 4)
        values = table.column("amount").to_pylist()
        assert values == amounts * repetitions == frame["amount"].tolist()
        assert all(v.as_tuple().exponent == -4 for v in values)
        return state, frame, manifest

    # Unkeyed APPEND deliberately permits distinct logical deliveries of the
    # same physical source. Each arrival must contribute exactly two rows.
    a = apply(append_pipeline)
    a_id = a["application_id"]
    a_state, a_frame, _ = read_current(a, 1)
    baseline = snapshot()
    assert a["status"] == "SUCCESS" and a["inserted_rows"] == 2
    assert baseline["head"]["current_state_id"] == a_state["state_id"] == a["result_state_id"]
    b_upload = second_delivery(append_pipeline[0])
    original_validate = store.validate_state
    observed = []

    def validate_then_abandon(key, checksum, application, *args, **kwargs):
        verified = original_validate(key, checksum, application, *args, **kwargs)
        if application["upload_request_id"] == b_upload:
            current = snapshot()
            b_id = application["application_id"]
            candidate = next(s for s in current["states"].values() if s["source_application_id"] == b_id)
            assert current["head"] == baseline["head"]
            assert current["apps"][b_id]["status"] == "RUNNING"
            assert candidate["status"] == "STAGED" and candidate["manifest_key"] == key
            assert candidate["previous_state_id"] == a_state["state_id"]
            assert len(verified) == 4
            observed.append((b_id, candidate["state_id"]))
            raise WorkerInterrupted("abandon B with predecessor A")
        return verified

    with monkeypatch.context() as interruption:
        interruption.setattr(store, "validate_state", validate_then_abandon)
        with pytest.raises(WorkerInterrupted, match="predecessor A"):
            apply(append_pipeline, b_upload)
    assert len(observed) == 1
    b_id, stale_id = observed[0]
    abandoned = snapshot()
    stale = abandoned["states"][stale_id]
    pinned_b = abandoned["deliveries"][b_id]
    stale_manifest = json.loads(objects[stale["manifest_key"]])
    assert stale_manifest["previous_state_id"] == a_state["state_id"]
    assert stale_manifest["delivery_manifest_id"] == pinned_b["manifest_id"]
    assert pinned_b["manifest_version"] == stale_manifest["manifest_version"] == 2
    assert abandoned["apps"][b_id]["status"] == "RUNNING"
    assert abandoned["apps"][b_id]["result_state_id"] is None
    assert abandoned["head"] == baseline["head"]

    # B's locks have unwound; C uses the ordinary coordinator and ownership
    # checks. No lifecycle statuses, predecessor IDs or constraints are edited.
    c_upload = second_delivery(append_pipeline[0])
    assert len({a["upload_request_id"], b_upload, c_upload}) == 3
    c = apply(append_pipeline, c_upload)
    c_id = c["application_id"]
    assert len({a_id, b_id, c_id}) == 3
    assert c["status"] == "SUCCESS" and c["inserted_rows"] == 2
    c_state, c_frame, c_manifest = read_current(c, 2)
    after_c = snapshot()
    assert c_state["state_id"] == c["result_state_id"] == after_c["head"]["current_state_id"]
    assert c_state["previous_state_id"] == a_state["state_id"]
    assert c_state["source_application_id"] == c_id
    assert after_c["apps"][b_id] == abandoned["apps"][b_id]
    assert after_c["states"][stale_id] == stale
    assert after_c["deliveries"][b_id] == pinned_b
    assert c_frame["_datarise_upload_id"].tolist() == [a["upload_request_id"]] * 2 + [c_upload] * 2
    pd.testing.assert_frame_equal(c_frame.iloc[:2].reset_index(drop=True), a_frame)

    with connect() as conn:
        source_key = conn.execute(
            "SELECT silver_path FROM data_quality_runs WHERE dq_run_id=%s",
            (abandoned["apps"][b_id]["source_dq_run_id"],),
        ).fetchone()[0]
    objects[source_key] = b"upstream Silver changed after B was pinned and C published"
    before_recovery_objects = dict(objects)
    original_read = store.read
    reads = []

    def read_pinned(key, *args, **kwargs):
        assert key != source_key, "Recovery reread mutable upstream Silver"
        reads.append(key)
        return original_read(key, *args, **kwargs)

    with monkeypatch.context() as recovery:
        recovery.setattr(store, "read", read_pinned)
        b = apply(append_pipeline, b_upload)
    assert b["application_id"] == b_id and b["status"] == "SUCCESS"
    assert b["failure_code"] is None and b["completed_at"] is not None
    assert (b["inserted_rows"], b["duplicate_rows"], b["incremental_rejected_rows"],
            b["current_state_rows"]) == (2, 0, 0, 6)
    assert pinned_b["manifest_key"] in reads and pinned_b["silver_key"] in reads
    assert c_state["manifest_key"] in reads and c_manifest["data_key"] in reads
    b_state, final_frame, b_manifest = read_current(b, 3)
    final = snapshot()
    assert b["result_state_id"] == b_state["state_id"] == final["head"]["current_state_id"]
    assert b_state["state_id"] != stale_id
    assert b_state["previous_state_id"] == c_state["state_id"]
    assert b_state["source_application_id"] == b_id
    assert b_manifest["previous_state_id"] == c_state["state_id"]
    assert b_manifest["source_state_version"] == c_state["state_version"]
    assert b_manifest["delivery_manifest_id"] == pinned_b["manifest_id"]
    assert final["states"][stale_id] == stale and stale["status"] == "STAGED"
    assert final["states"][c_state["state_id"]] == after_c["states"][c_state["state_id"]]
    assert final["states"][a_state["state_id"]] == baseline["states"][a_state["state_id"]]
    assert final["apps"][a_id] == baseline["apps"][a_id]
    assert final["apps"][c_id] == after_c["apps"][c_id]
    assert len(final["apps"]) == 3 and all(app["status"] == "SUCCESS" for app in final["apps"].values())
    assert len(final["states"]) == 4
    assert [s["source_application_id"] for s in final["states"].values() if s["status"] == "PUBLISHED"] == [a_id, c_id, b_id]
    assert final["deliveries"] == after_c["deliveries"]
    assert all(objects[key] == body for key, body in before_recovery_objects.items())
    for pin in ("upload_request_id", "dataset_version_id", "dataset_version_file_id",
                "policy_id", "applied_rule_version", "source_dq_run_id", "ingestion_time"):
        assert final["apps"][b_id][pin] == abandoned["apps"][b_id][pin]
    # Compare every retained value and lineage field, not just aggregate counts.
    pd.testing.assert_frame_equal(final_frame.iloc[:4].reset_index(drop=True), c_frame)
    assert final_frame["id"].tolist() == a_frame["id"].tolist() * 3
    assert final_frame["_datarise_upload_id"].tolist() == [a["upload_request_id"]] * 2 + [c_upload] * 2 + [b_upload] * 2
    assert final_frame["_datarise_application_id"].tolist() == [a_id] * 2 + [c_id] * 2 + [b_id] * 2
    assert final_frame["_datarise_policy_id"].tolist() == [policy["policy_id"]] * 6
    assert final_frame["_datarise_rule_version"].tolist() == [1] * 6

    final_objects = dict(objects)
    assert apply(append_pipeline, b_upload) == b
    assert apply(append_pipeline, c_upload) == c  # Historical success cannot rewind the head.
    assert snapshot() == final and objects == final_objects
