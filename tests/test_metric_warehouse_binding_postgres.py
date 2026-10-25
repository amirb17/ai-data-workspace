"""Disposable PostgreSQL tests for metric -> warehouse binding resolution."""
import psycopg

from app.db import (
    warehouse_repository,
    warehouse_reliability_repository,
)
from app.services.metric_warehouse_binding_service import (
    _context,
    _physical_table,
    resolve_metric_warehouse_sources,
)
from app.warehouse.accepted_changes import (
    digest,
)
from app.warehouse.providers import (
    model_adapter,
)
from app.warehouse.serving_materialization import (
    materialization_identity,
)
import pytest
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from app.processing.metric_execution import (
    MetricExecutionPlan,
)
from app.services.metric_warehouse_binding_service import (
    resolve_metric_warehouse_sources,
)

from tests.test_warehouse_provider_postgres import (
    fact_provider_pg,
    provider_pg,
    publish,
    work,
)


def metric_plan(
    workspace_id,
    dataset_id,
    state_id,
):
    return MetricExecutionPlan(
        workspace_id=workspace_id,
        candidate_id=1,
        definition={},
        sources=(
            {
                "dataset_id": (
                    dataset_id
                ),
                "state_id": state_id,
                "gold_run_id": 1,
                "structural_key": (
                    "a" * 64
                ),
                "semantic_key": (
                    "b" * 64
                ),
            },
        ),
        joins=(),
        signature="c" * 64,
    )


def publish_warehouse(f):
    intent, claim, logical = work(
        f
    )

    response = f.provider.execute(
        intent["operation_id"],
        claim["run_id"],
        claim["fence"],
    )

    publication = publish(
        f,
        intent,
        claim,
        response,
    )

    state_id = (
        logical
        .manifests[0]
        ["pin"]
        ["state_id"]
    )

    return (
        logical,
        publication,
        state_id,
    )

def record_serving_ready(
    f,
    publication,
    **overrides,
):
    with f.pg["connect"]() as conn:
        rows = (
            warehouse_repository
            .metric_warehouse_bindings(
                conn,
                f.pg["workspace"],
                [
                    f.pg["dataset"],
                ],
            )
        )

        assert len(rows) == 1

        row = rows[0]

        context = _context(row)

        table = _physical_table(
            row,
            f.pg["workspace"],
        )

        configuration_sha256 = digest(
            row["configuration"]
        )

        schema_sha256 = (
            model_adapter.schema_hash(
                context
            )
        )

        source_vector_sha256 = digest(
            row["source_vector"]
        )

        identity = (
            materialization_identity(
                workspace_id=(
                    f.pg["workspace"]
                ),
                model_id=f.model,
                model_version_id=(
                    f.version
                ),
                resource_id=f.resource,
                generation=row[
                    "generation"
                ],
                publication_id=publication[
                    "publication_id"
                ],
                publication_revision=(
                    publication[
                        "revision"
                    ]
                ),
                physical_table=table,
                configuration_sha256=(
                    configuration_sha256
                ),
                schema_sha256=(
                    schema_sha256
                ),
                source_vector_sha256=(
                    source_vector_sha256
                ),
            )
        )

        evidence = {
            "workspace_id": (
                f.pg["workspace"]
            ),
            "warehouse_model_id": (
                f.model
            ),
            "warehouse_model_version_id": (
                f.version
            ),
            "resource_id": (
                f.resource
            ),
            "publication_id": (
                publication[
                    "publication_id"
                ]
            ),
            "publication_revision": (
                publication[
                    "revision"
                ]
            ),
            "generation": (
                row["generation"]
            ),
            "materialization_token": (
                identity[
                    "materialization_token"
                ]
            ),
            "intent_sha256": (
                identity[
                    "intent_sha256"
                ]
            ),
            "configuration_sha256": (
                configuration_sha256
            ),
            "schema_sha256": (
                schema_sha256
            ),
            "source_vector_sha256": (
                source_vector_sha256
            ),
            "provider_job_id": (
                "test-serving-job-"
                + str(
                    publication[
                        "publication_id"
                    ]
                )
            ),
            "serving_sha256": digest(
                [
                    "test-serving",
                    publication[
                        "publication_id"
                    ],
                ]
            ),
            "row_count": 1,
            "bytes_processed": 0,
            "bytes_billed": 0,
        }

        evidence.update(
            overrides
        )

        return (
            warehouse_reliability_repository
            .record_serving_materialization(
                conn,
                evidence,
            )
        )
def test_fact_binding_resolves_exact_published_state(
    fact_provider_pg,
):
    f = fact_provider_pg

    (
        logical,
        publication,
        state_id,
    ) = publish_warehouse(f)
    receipt = record_serving_ready(
    f,
    publication,
)
    plan = metric_plan(
        f.pg["workspace"],
        f.pg["dataset"],
        state_id,
    )

    with f.pg["connect"]() as conn:
        resolved = (
            resolve_metric_warehouse_sources(
                conn,
                plan,
            )
        )

    assert set(resolved) == {
        f.pg["dataset"]
    }

    source = resolved[
        f.pg["dataset"]
    ]

    assert source.model_type == "FACT"

    assert source.state_id == state_id

    assert (
        source.publication_id
        == publication[
            "publication_id"
        ]
    )

    assert (
        source.publication_revision
        == publication["revision"]
    )

    assert source.grain_keys == (
        "id",
    )

    assert (
        source.event_time_column
        == "effective"
    )

    assert (
        source.event_time_interpretation
        == "UTC"
    )

    assert source.physical_table == (
        "synthetic-project."
        f"dr_dev_w{f.pg['workspace']}."
        f"m{f.model}_v{f.version}_g1_p{publication['publication_id']}_r{publication['revision']}"
    )
    assert (
    source.serving_materialization_id
    == receipt[
        "serving_materialization_id"
        ]
    )

    assert (
        source.serving_sha256
        == receipt[
            "serving_sha256"
        ]
    )

    mapping = {
        logical_name: (
            physical_name,
            kind,
        )
        for (
            logical_name,
            physical_name,
            kind,
        )
        in source.columns
    }

    assert mapping["line"][1] == (
        "INT64"
    )

    assert "float_id" not in mapping


def test_dimension_binding_resolves_business_key(
    provider_pg,
):
    f = provider_pg

    (
        _,
        publication,
        state_id,
    ) = publish_warehouse(f)
    record_serving_ready(
    f,
    publication,
)

    plan = metric_plan(
        f.pg["workspace"],
        f.pg["dataset"],
        state_id,
    )

    with f.pg["connect"]() as conn:
        resolved = (
            resolve_metric_warehouse_sources(
                conn,
                plan,
            )
        )

    source = resolved[
        f.pg["dataset"]
    ]

    assert (
        source.model_type
        == "DIMENSION"
    )

    assert source.business_keys

    assert source.grain_keys == ()

    assert (
        source.publication_id
        == publication[
            "publication_id"
        ]
    )


def test_stale_metric_state_does_not_reuse_newer_publication(
    fact_provider_pg,
):
    f = fact_provider_pg

    (
    _,
    publication,
    state_id,
    ) = publish_warehouse(f)

    record_serving_ready(
        f,
        publication,
    )

    plan = metric_plan(
        f.pg["workspace"],
        f.pg["dataset"],
        state_id + 1,
    )

    with (
        f.pg["connect"]()
        as conn,
        pytest.raises(
            ValueError,
            match=(
                "WAREHOUSE_PUBLICATION_NOT_CURRENT"
            ),
        ),
    ):
        resolve_metric_warehouse_sources(
            conn,
            plan,
        )


def test_unpublished_resource_is_not_warehouse_eligible(
    fact_provider_pg,
):
    f = fact_provider_pg

    plan = metric_plan(
        f.pg["workspace"],
        f.pg["dataset"],
        1,
    )

    with (
        f.pg["connect"]()
        as conn,
        pytest.raises(
            ValueError,
            match=(
                "WAREHOUSE_PUBLICATION_NOT_CURRENT"
            ),
        ),
    ):
        resolve_metric_warehouse_sources(
            conn,
            plan,
        )


def test_production_binding_is_not_silently_used():
    plan = MetricExecutionPlan(
        workspace_id=1,
        candidate_id=1,
        definition={},
        sources=(
            {
                "dataset_id": 1,
                "state_id": 1,
            },
        ),
        joins=(),
        signature="d" * 64,
    )

    with pytest.raises(
        ValueError,
        match=(
            "WAREHOUSE_ENVIRONMENT_UNSUPPORTED"
        ),
    ):
        resolve_metric_warehouse_sources(
            None,
            plan,
            environment="production",
        )
def test_published_head_without_serving_receipt_is_not_queryable(
    fact_provider_pg,
):
    f = fact_provider_pg

    (
        _,
        _,
        state_id,
    ) = publish_warehouse(f)

    plan = metric_plan(
        f.pg["workspace"],
        f.pg["dataset"],
        state_id,
    )

    with (
        f.pg["connect"]()
        as conn,
        pytest.raises(
            ValueError,
            match=(
                "WAREHOUSE_SERVING_NOT_READY"
            ),
        ),
    ):
        resolve_metric_warehouse_sources(
            conn,
            plan,
        )
def test_serving_receipt_contract_mismatch_fails_closed(
    fact_provider_pg,
):
    f = fact_provider_pg

    (
        _,
        publication,
        state_id,
    ) = publish_warehouse(f)

    record_serving_ready(
        f,
        publication,
        source_vector_sha256=(
            "f" * 64
        ),
    )

    plan = metric_plan(
        f.pg["workspace"],
        f.pg["dataset"],
        state_id,
    )

    with (
        f.pg["connect"]()
        as conn,
        pytest.raises(
            ValueError,
            match=(
                "WAREHOUSE_SERVING_EVIDENCE_INVALID"
            ),
        ),
    ):
        resolve_metric_warehouse_sources(
            conn,
            plan,
        )
def test_serving_receipt_is_immutable(
    fact_provider_pg,
):
    f = fact_provider_pg

    (
        _,
        publication,
        _,
    ) = publish_warehouse(f)

    receipt = record_serving_ready(
        f,
        publication,
    )

    statements = (
        (
            """
            UPDATE warehouse_serving_materializations
            SET row_count=row_count+1
            WHERE serving_materialization_id=%s
            """
        ),
        (
            """
            DELETE FROM warehouse_serving_materializations
            WHERE serving_materialization_id=%s
            """
        ),
    )

    for statement in statements:
        with (
            pytest.raises(
                psycopg.Error
            ),
            f.pg["connect"]()
            as conn,
        ):
            conn.execute(
                statement,
                (
                    receipt[
                        "serving_materialization_id"
                    ],
                ),
            )

    with (
        pytest.raises(
            psycopg.Error
        ),
        f.pg["connect"]()
        as conn,
    ):
        conn.execute(
            """
            TRUNCATE
            warehouse_serving_materializations
            """
        )
