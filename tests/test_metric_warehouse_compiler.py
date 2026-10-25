import pytest

from app.processing.metric_execution import (
    MetricExecutionPlan,
)
from app.processing.metric_warehouse_compiler import (
    MetricWarehouseSource,
    compile_metric_query,
    decode_metric_query_result,
)
from app.query_governance.providers.base import (
    QueryExecutionResult,
)


TABLE = (
    "synthetic-project."
    "dr_dev_w1."
    "m20_v30_g1"
)


def source(**updates):
    value = dict(
        dataset_id=10,
        state_id=100,
        model_id=20,
        model_version_id=30,
        resource_id=40,
        generation=1,
        publication_id=50,
        publication_revision=2,
        serving_materialization_id=60,
        serving_sha256="9" * 64,
        serving_row_count=100,
        physical_table=TABLE,
        columns=(
        (
            "amount",
            "c0",
            "BIGNUMERIC",
        ),
        (
            "category",
            "c1",
            "STRING",
        ),
        (
            "event_time",
            "c2",
            "TIMESTAMP",
        ),
        (
            "id",
            "c3",
            "STRING",
        ),
        (
            "customer_id",
            "c4",
            "STRING",
        ),
    ),
        grain_keys=("id",),
        configuration_sha256="a" * 64,
        source_vector_sha256="b" * 64,
        event_time_column="event_time",
        event_time_interpretation="UTC",
    )

    value.update(updates)

    return MetricWarehouseSource(
        **value
    )

def dimension_source(**updates):
    value = dict(
        dataset_id=11,
        state_id=110,
        model_id=21,
        model_version_id=31,
        resource_id=41,
        generation=1,
        publication_id=51,
        publication_revision=3,
        serving_materialization_id=61,
        serving_sha256="8" * 64,
        serving_row_count=20,
        physical_table=(
            "synthetic-project."
            "dr_dev_w1."
            "m21_v31_g1"
        ),
        columns=(
            (
                "customer_id",
                "c0",
                "STRING",
            ),
            (
                "region",
                "c1",
                "STRING",
            ),
        ),
        grain_keys=(),
        business_keys=(
            "customer_id",
        ),
        configuration_sha256=(
            "f" * 64
        ),
        source_vector_sha256=(
            "1" * 64
        ),
        model_type="DIMENSION",
    )

    value.update(updates)

    return MetricWarehouseSource(
        **value
    )
def definition(
    *,
    metric_type="SUM",
    nodes=None,
    root="total",
    dimensions=None,
    time=False,
    grain="RECORD",
    grain_keys=None,
):
    nodes = nodes or [
        {
            "id": "amount",
            "op": "COLUMN",
            "column": {
                "dataset_id": 10,
                "column": "amount",
            },
        },
        {
            "id": "total",
            "op": "SUM",
            "args": ["amount"],
        },
    ]

    return {
        "name": "Total amount",
        "description": "Exact total amount",
        "metric_type": metric_type,
        "base_dataset_id": 10,
        "base_entity": "Order",
        "grain": grain,
        "grain_keys": (
            grain_keys or []
        ),
        "formula": {
            "nodes": nodes,
            "root": root,
            "zero_denominator": "NULL",
            "scale": 1,
        },
        "dimensions": (
            dimensions or []
        ),
        "time_field": (
            {
                "dataset_id": 10,
                "column": "event_time",
            }
            if time
            else None
        ),
        "time_bucket": (
            "MONTH"
            if time
            else None
        ),
        "relationship_ids": [],
        "default_filters": [],
        "unit": (
            "RATIO"
            if metric_type == "RATIO"
            else "NUMBER"
        ),
        "confidence": 1.0,
        "rationale": "Test definition",
        "unresolved_assumptions": [],
    }


def plan(**updates):
    value = dict(
        workspace_id=1,
        candidate_id=2,
        definition=definition(),
        sources=(
            {
                "dataset_id": 10,
                "state_id": 100,
                "gold_run_id": 200,
                "structural_key": "c" * 64,
                "semantic_key": "d" * 64,
            },
        ),
        joins=(),
        signature="e" * 64,
    )

    value.update(updates)

    return MetricExecutionPlan(
        **value
    )


def test_sum_compiles_to_exact_fact_column():
    compiled = compile_metric_query(
        plan(),
        {
            10: source(),
        },
    )

    assert compiled.physical_tables == (
        TABLE,
    )

    assert (
        "SUM(f.c0) AS __metric_value"
        in compiled.sql
    )

    assert (
        "WHERE f.is_active = TRUE"
        in compiled.sql
    )

    assert (
        "COUNT(*) AS __input_rows"
        in compiled.sql
    )

    assert len(
        compiled.request_token
    ) == 64

    assert len(
        compiled.intent_sha256
    ) == 64


def test_publication_tables_remain_physically_and_logically_pinned():
    a = source(physical_table='synthetic-project.dr_dev_w1.m20_v30_g1_p50_r2')
    b = source(physical_table='synthetic-project.dr_dev_w1.m20_v30_g1_p54_r3',
               publication_id=54, publication_revision=3, serving_materialization_id=61)
    first = compile_metric_query(plan(), {10: a})
    second = compile_metric_query(plan(), {10: b})
    assert first.physical_tables == (a.physical_table,)
    assert second.physical_tables == (b.physical_table,)
    assert first.request_token != second.request_token
    assert first.intent_sha256 != second.intent_sha256


def test_compilation_is_deterministic():
    first = compile_metric_query(
        plan(),
        {
            10: source(),
        },
    )

    second = compile_metric_query(
        plan(),
        {
            10: source(),
        },
    )

    assert first == second


def test_grouped_metric_is_bounded_and_ordered():
    metric = definition(
        dimensions=[
            {
                "dataset_id": 10,
                "column": "category",
            }
        ],
    )

    compiled = compile_metric_query(
        plan(
            definition=metric,
        ),
        {
            10: source(),
        },
    )

    assert (
        "f.c1 AS __group_0"
        in compiled.sql
    )

    assert (
        "COUNT(*) OVER () AS __total_groups"
        in compiled.sql
    )

    assert (
        "ORDER BY __group_0 ASC NULLS LAST"
        in compiled.sql
    )

    assert "LIMIT 101" in compiled.sql


def test_time_bucket_uses_approved_event_calendar():
    metric = definition(
        time=True,
    )

    compiled = compile_metric_query(
        plan(
            definition=metric,
        ),
        {
            10: source(),
        },
    )

    assert (
        "DATETIME_TRUNC("
        "DATETIME(f.c2, 'UTC'), MONTH)"
        in compiled.sql
    )


def test_count_compiles_without_inventing_column():
    metric = definition(
        metric_type="COUNT",
        nodes=[
            {
                "id": "count_rows",
                "op": "COUNT",
            }
        ],
        root="count_rows",
    )

    compiled = compile_metric_query(
        plan(
            definition=metric,
        ),
        {
            10: source(),
        },
    )

    assert (
        "COUNT(*) AS __metric_value"
        in compiled.sql
    )


def test_divide_compiles_zero_denominator_guard():
    metric = definition(
        metric_type="RATIO",
        nodes=[
            {
                "id": "amount",
                "op": "COLUMN",
                "column": {
                    "dataset_id": 10,
                    "column": "amount",
                },
            },
            {
                "id": "sum_amount",
                "op": "SUM",
                "args": ["amount"],
            },
            {
                "id": "count_rows",
                "op": "COUNT",
            },
            {
                "id": "ratio",
                "op": "DIVIDE",
                "args": [
                    "sum_amount",
                    "count_rows",
                ],
            },
        ],
        root="ratio",
    )

    compiled = compile_metric_query(
        plan(
            definition=metric,
        ),
        {
            10: source(),
        },
    )

    assert "CASE WHEN" in compiled.sql
    assert "count_rows" not in compiled.sql
    assert "= 0 THEN NULL" in compiled.sql


def test_stale_warehouse_state_is_rejected():
    with pytest.raises(
        ValueError,
        match="WAREHOUSE_SOURCE_PIN_MISMATCH",
    ):
        compile_metric_query(
            plan(),
            {
                10: source(
                    state_id=101,
                ),
            },
        )


def test_unapproved_column_is_rejected():
    metric = definition(
        nodes=[
            {
                "id": "secret",
                "op": "COLUMN",
                "column": {
                    "dataset_id": 10,
                    "column": "secret",
                },
            },
            {
                "id": "total",
                "op": "SUM",
                "args": ["secret"],
            },
        ],
    )

    with pytest.raises(
        ValueError,
        match="WAREHOUSE_COLUMN_NOT_APPROVED",
    ):
        compile_metric_query(
            plan(
                definition=metric,
            ),
            {
                10: source(),
            },
        )


def test_relationship_is_not_silently_compiled():
    changed = plan(
        joins=(
            {
                "relationship_id": 9,
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match="WAREHOUSE_JOIN_NOT_SUPPORTED",
    ):
        compile_metric_query(
            changed,
            {
                10: source(),
            },
        )


def test_entity_grain_must_match_fact_grain():
    metric = definition(
        grain="ENTITY",
        grain_keys=["other_id"],
    )

    with pytest.raises(
        ValueError,
        match="GRAIN_MISMATCH",
    ):
        compile_metric_query(
            plan(
                definition=metric,
            ),
            {
                10: source(),
            },
        )
def joined_plan(**updates):
    metric = definition(
        dimensions=[
            {
                "dataset_id": 11,
                "column": "region",
            }
        ],
    )

    value = dict(
        workspace_id=1,
        candidate_id=3,
        definition=metric,
        sources=(
            {
                "dataset_id": 10,
                "state_id": 100,
                "gold_run_id": 200,
                "structural_key": "c" * 64,
                "semantic_key": "d" * 64,
            },
            {
                "dataset_id": 11,
                "state_id": 110,
                "gold_run_id": 201,
                "structural_key": "2" * 64,
                "semantic_key": "3" * 64,
            },
        ),
        joins=(
            {
                "relationship_id": 90,
                "version": 1,
                "parent": {
                    "dataset_id": 11,
                    "columns": [
                        "customer_id"
                    ],
                },
                "child": {
                    "dataset_id": 10,
                    "columns": [
                        "customer_id"
                    ],
                },
                "cardinality": (
                    "ONE_TO_MANY"
                ),
            },
        ),
        signature="4" * 64,
    )

    value.update(updates)

    return MetricExecutionPlan(
        **value
    )
def test_one_hop_dimension_compiles_safe_current_join():
    compiled = compile_metric_query(
        joined_plan(),
        {
            10: source(),
            11: dimension_source(),
        },
    )

    assert compiled.physical_tables == (
        TABLE,
        dimension_source().physical_table,
    )

    assert (
        "LEFT JOIN "
        "`synthetic-project."
        "dr_dev_w1."
        "m21_v31_g1` AS d0"
        in compiled.sql
    )

    assert (
        "f.c4 = d0.c0"
        in compiled.sql
    )

    assert (
        "d0.is_current = TRUE"
        in compiled.sql
    )

    assert (
        "d0.c1 AS __group_0"
        in compiled.sql
    )

    assert (
        "__missing_dimension_rows"
        in compiled.sql
    )


def test_dimension_join_requires_exact_business_key():
    with pytest.raises(
        ValueError,
        match="DIMENSION_KEY_MISMATCH",
    ):
        compile_metric_query(
            joined_plan(),
            {
                10: source(),
                11: dimension_source(
                    business_keys=(
                        "region",
                    ),
                ),
            },
        )


def test_dimension_time_semantics_are_not_invented():
    metric = definition(
        time=False,
    )

    metric["time_field"] = {
        "dataset_id": 11,
        "column": "region",
    }

    metric["time_bucket"] = "MONTH"

    changed = joined_plan(
        definition=metric,
    )

    with pytest.raises(
        ValueError,
        match="TIME_SEMANTICS_UNRESOLVED",
    ):
        compile_metric_query(
            changed,
            {
                10: source(),
                11: dimension_source(),
            },
        )


def query_result(rows):
    return QueryExecutionResult(
        provider_job_id="job-1",
        rows=tuple(rows),
        truncated=False,
        bytes_processed=100,
        bytes_billed=100,
        payload_bytes=100,
        duration_ms=10,
    )


def test_scalar_query_result_decodes_existing_metric_contract():
    output = decode_metric_query_result(
        plan(),
        query_result(
            [
                {
                    "__metric_value": 25,
                    "__input_rows": 4,
                    "__missing_dimension_rows": 0,
                }
            ]
        ),
    )

    assert output == {
        "kind": "scalar",
        "value": 25,
        "groups": [],
        "input_rows": 4,
        "total_groups": 0,
        "truncated": False,
        "display_hint": "scalar",
    }


def test_grouped_query_result_decodes_existing_metric_contract():
    output = decode_metric_query_result(
        joined_plan(),
        query_result(
            [
                {
                    "__group_0": "East",
                    "__metric_value": 20,
                    "__input_rows": 5,
                    "__total_groups": 2,
                    "__missing_dimension_rows": 0,
                },
                {
                    "__group_0": "West",
                    "__metric_value": 30,
                    "__input_rows": 5,
                    "__total_groups": 2,
                    "__missing_dimension_rows": 0,
                },
            ]
        ),
    )

    assert output == {
        "kind": "grouped",
        "value": None,
        "groups": [
            {
                "dimensions": [
                    "East"
                ],
                "value": 20,
            },
            {
                "dimensions": [
                    "West"
                ],
                "value": 30,
            },
        ],
        "input_rows": 5,
        "total_groups": 2,
        "truncated": False,
        "display_hint": "table",
    }


def test_missing_dimension_coverage_fails_closed():
    with pytest.raises(
        ValueError,
        match="JOIN_COVERAGE",
    ):
        decode_metric_query_result(
            joined_plan(),
            query_result(
                [
                    {
                        "__group_0": None,
                        "__metric_value": 20,
                        "__input_rows": 5,
                        "__total_groups": 1,
                        "__missing_dimension_rows": 1,
                    }
                ]
            ),
        )


def test_grouped_result_truncates_at_metric_contract_limit():
    rows = [
        {
            "__group_0": f"G{i:03d}",
            "__metric_value": i,
            "__input_rows": 101,
            "__total_groups": 101,
            "__missing_dimension_rows": 0,
        }
        for i in range(101)
    ]

    output = decode_metric_query_result(
        joined_plan(),
        query_result(rows),
    )

    assert len(
        output["groups"]
    ) == 100

    assert output["total_groups"] == 101
    assert output["truncated"] is True
