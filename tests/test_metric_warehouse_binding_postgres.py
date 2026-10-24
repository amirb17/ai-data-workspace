"""Disposable PostgreSQL tests for metric -> warehouse binding resolution."""

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


def test_fact_binding_resolves_exact_published_state(
    fact_provider_pg,
):
    f = fact_provider_pg

    (
        logical,
        publication,
        state_id,
    ) = publish_warehouse(f)

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
        f"m{f.model}_v{f.version}_g1"
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
        _,
        state_id,
    ) = publish_warehouse(f)

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