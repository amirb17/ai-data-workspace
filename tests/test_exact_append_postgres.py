import io
import json
from decimal import Decimal

import pandas as pd
import pytest
import hashlib
from app.processing.exact_parquet import write_exact_parquet
from app.services import append_application_service as execution

# Register the complete PostgreSQL fixture dependency chain.
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline

from tests.test_append_execution import append_pipeline, apply
from tests.test_incremental_foundation import second_delivery


def exact_rules_for(version_id):
    return [
        (
            None,
            version_id,
            "amount",
            "DATA_TYPE",
            {
                "type": "DECIMAL",
                "mode": "EXACT",
                "precision": 20,
                "scale": 4,
            },
            True,
        )
    ]


def prepare_exact_test_source(fixture, monkeypatch):
    pipeline, policy, _ = fixture
    database = pipeline[0]
    objects = pipeline[4]

    version_id = pipeline[3]["dataset_version"]["dataset_version_id"]
    file_id = pipeline[3]["file"][0]

    rules = exact_rules_for(version_id)

    source_key = (
        f"silver/dataset_version_id={version_id}/"
        f"file_id={file_id}/rule_version=1/data.parquet"
    )

    # Synthetic fixture substitution only.
    # Not valid as a production Silver provenance mechanism.
    source = pd.read_parquet(
        io.BytesIO(objects[source_key])
    )

    assert len(source) == 2

    source["amount"] = pd.Series(
        [
            Decimal("10.0000"),
            Decimal("20.0000"),
        ],
        index=source.index,
        dtype=object,
    )

    columns = [
        column["name"]
        for column in policy["schema_columns"]
    ]

    payload, _ = write_exact_parquet(
        source,
        rules,
        columns,
    )

    objects[source_key] = payload
        # Synthetic test fixture only:
    # simulate a Silver DQ run that produced these
    # exact bytes before APPEND begins.
    with database[0]() as conn:
        updated = conn.execute(
            """
            UPDATE data_quality_runs
            SET silver_sha256 = %s
            WHERE dataset_version_file_id = %s
              AND rule_version = 1
              AND silver_path = %s
            """,
            (
                hashlib.sha256(payload).hexdigest(),
                pipeline[3]["dataset_version_file"][0],
                source_key,
            ),
        )

        assert updated.rowcount == 1
    monkeypatch.setattr(
        execution,
        "get_approved_policy",
        lambda dataset_version_id, rule_version: (
            rules
            if dataset_version_id == version_id
            and rule_version == 1
            else []
        ),
    )

    return database, objects, policy, rules


def test_exact_append_two_deliveries_and_retry(
    append_pipeline,
    monkeypatch,
):
    db, objects, policy, rules = prepare_exact_test_source(
        append_pipeline,
        monkeypatch,
    )

    monkeypatch.setenv(
        "DATARISE_EXACT_APPEND_V2_TEST_ONLY",
        "1",
    )

    # First APPEND publication.
    first = apply(append_pipeline)

    assert first["status"] == "SUCCESS"
    assert first["current_state_rows"] == 2
    assert first["inserted_rows"] == 2

    # A second logical delivery reuses the same physical
    # source but must create a new application/state.
    upload_id = second_delivery(
        append_pipeline[0]
    )

    second = apply(
        append_pipeline,
        upload_id,
    )

    assert second["status"] == "SUCCESS"
    assert second["current_state_rows"] == 4
    assert second["inserted_rows"] == 2
        # Verify that PostgreSQL records both deliveries as V2.
    with db[0]() as conn:
        versions = conn.execute(
            """
            SELECT dm.manifest_version
            FROM delivery_manifests dm
            JOIN delivery_applications da
              ON da.application_id = dm.application_id
            WHERE da.dataset_id = %s
            ORDER BY da.application_id
            """,
            (db[3],),
        ).fetchall()

    assert versions == [(2,), (2,)]
    with db[0]() as conn:
        row = conn.execute(
            """
            SELECT s.manifest_key
            FROM datasets d
            JOIN dataset_state_versions s
              ON s.state_id = d.current_state_id
            WHERE d.dataset_id = %s
            """,
            (db[3],),
        ).fetchone()

    assert row is not None

    manifest = json.loads(
        objects[row[0]]
    )

    assert manifest["manifest_version"] == 2
    assert manifest["exact_descriptor"]["manifest_version"] == 2

    store = execution.IncrementalArtifacts()

    state, frame = execution.current_state(
        store,
        second,
        policy,
        exact_rules=rules,
    )

    assert state["status"] == "PUBLISHED"

    assert [
        str(value)
        for value in frame["amount"]
    ] == [
        "10.0000",
        "20.0000",
        "10.0000",
        "20.0000",
    ]

    # A successful retry must not create new objects.
    keys_before = set(objects)

    repeated = apply(
        append_pipeline,
        upload_id,
    )

    assert repeated == second
    assert set(objects) == keys_before


def test_exact_append_disabled_without_explicit_test_gate(
    append_pipeline,
    monkeypatch,
):
    pipeline, policy, _ = append_pipeline

    version_id = pipeline[3]["dataset_version"]["dataset_version_id"]

    monkeypatch.setattr(
        execution,
        "get_approved_policy",
        lambda *_: exact_rules_for(version_id),
    )

    monkeypatch.delenv(
        "DATARISE_EXACT_APPEND_V2_TEST_ONLY",
        raising=False,
    )

    with pytest.raises(ValueError, match="disabled"):
        apply(append_pipeline)

    db = pipeline[0]

    with db[0]() as conn:
        head = conn.execute(
            """
            SELECT current_state_id
            FROM datasets
            WHERE dataset_id = %s
            """,
            (db[3],),
        ).fetchone()[0]

        state_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM dataset_state_versions
            WHERE dataset_id = %s
            """,
            (db[3],),
        ).fetchone()[0]

    assert head is None
    assert state_count == 0

def test_exact_append_failure_retries_from_immutable_delivery(
    append_pipeline,
    monkeypatch,
):
    db, objects, policy, rules = prepare_exact_test_source(
        append_pipeline,
        monkeypatch,
    )

    monkeypatch.setenv(
        "DATARISE_EXACT_APPEND_V2_TEST_ONLY",
        "1",
    )

    original_append = execution.append_rows

    # Simulate failure after the immutable delivery
    # manifest is registered but before publication.
    def fail_append(*args, **kwargs):
        raise RuntimeError("Simulated APPEND failure")

    monkeypatch.setattr(
        execution,
        "append_rows",
        fail_append,
    )

    with pytest.raises(
        RuntimeError,
        match="previous trusted state remains active",
    ):
        apply(append_pipeline)

    with db[0]() as conn:
        head = conn.execute(
            """
            SELECT current_state_id
            FROM datasets
            WHERE dataset_id = %s
            """,
            (db[3],),
        ).fetchone()[0]

        pinned = conn.execute(
            """
            SELECT manifest_key
            FROM delivery_manifests
            ORDER BY manifest_id DESC
            LIMIT 1
            """
        ).fetchone()

    assert head is None
    assert pinned is not None

    delivery_manifest = json.loads(
        objects[pinned[0]]
    )

    assert delivery_manifest["manifest_version"] == 2

    # Modify the original Silver object after
    # the delivery copy has already been pinned.
    source_key = (
        f"silver/dataset_version_id="
        f"{append_pipeline[0][3]['dataset_version']['dataset_version_id']}/"
        f"file_id={append_pipeline[0][3]['file'][0]}/"
        "rule_version=1/data.parquet"
    )

    objects[source_key] = b"modified original Silver"

    monkeypatch.setattr(
        execution,
        "append_rows",
        original_append,
    )

    # Retry must read the immutable delivery copy,
    # not the modified original Silver path.
    recovered = apply(append_pipeline)

    assert recovered["status"] == "SUCCESS"
    assert recovered["current_state_rows"] == 2
    assert recovered["inserted_rows"] == 2

    store = execution.IncrementalArtifacts()

    state, frame = execution.current_state(
        store,
        recovered,
        policy,
        exact_rules=rules,
    )

    assert state["status"] == "PUBLISHED"

    assert [str(v) for v in frame["amount"]] == [
        "10.0000",
        "20.0000",
    ]
def test_exact_append_rejects_modified_silver_before_first_delivery(
    append_pipeline,
    monkeypatch,
):
    db, objects, policy, rules = prepare_exact_test_source(
        append_pipeline,
        monkeypatch,
    )

    monkeypatch.setenv(
        "DATARISE_EXACT_APPEND_V2_TEST_ONLY",
        "1",
    )

    pipeline = append_pipeline[0]

    version_id = pipeline[3]["dataset_version"][
        "dataset_version_id"
    ]
    file_id = pipeline[3]["file"][0]

    source_key = (
        f"silver/dataset_version_id={version_id}/"
        f"file_id={file_id}/"
        "rule_version=1/data.parquet"
    )

    # Corrupt the original Silver object after
    # its successful DQ checksum was saved.
    objects[source_key] = b"modified Silver bytes"

    with pytest.raises(
        RuntimeError,
        match="previous trusted state remains active",
    ) as error:
        apply(append_pipeline)

    assert isinstance(error.value.__cause__, ValueError)
    assert "Silver source SHA-256" in str(
        error.value.__cause__
    )

    with db[0]() as conn:
        current_state_id = conn.execute(
            """
            SELECT current_state_id
            FROM datasets
            WHERE dataset_id = %s
            """,
            (db[3],),
        ).fetchone()[0]

        manifest_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM delivery_manifests dm
            JOIN delivery_applications da
              ON da.application_id = dm.application_id
            WHERE da.dataset_id = %s
            """,
            (db[3],),
        ).fetchone()[0]

    assert current_state_id is None
    assert manifest_count == 0