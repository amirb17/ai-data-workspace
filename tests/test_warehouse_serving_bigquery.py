from dataclasses import replace

import pytest

from app.warehouse.fact_planning import (
    compile_fact,
)
from app.warehouse.providers.base import (
    Binding,
    ErrorCode,
    Policy,
    ProviderError,
)
from app.warehouse.providers.bigquery import (
    BigQueryTarget,
)
from app.warehouse.serving_materialization import (
    ServingMaterializationRequest,
    configuration_digest,
    materialization_identity,
)
from app.warehouse.accepted_changes import (
    digest,
)
from app.warehouse.providers import (
    model_adapter,
)

from tests.bigquery_fake import (
    Client,
)
from tests.test_warehouse_fact_planning import (
    context as fact_context,
    evidence as fact_evidence,
)
from tests.test_warehouse_scd import (
    context as dimension_context,
    first as dimension_first,
)


def target(
    context,
    *,
    client=None,
    maximum_bytes=1 << 30,
    maximum_rows=1000,
    maximum_payload_bytes=8 << 20,
):
    binding = Binding(
        "synthetic-project",
        1,
        context.model_id,
        context.model_version_id,
        1,
        1,
    )

    result = BigQueryTarget(
        Policy(
            binding.project,
            enabled=True,
            maximum_bytes=maximum_bytes,
            maximum_rows=maximum_rows,
            maximum_payload_bytes=(
                maximum_payload_bytes
            ),
        ),
        binding,
        client or Client(),
    )

    result.provision(
        context
    )

    return result


def serving_request(
    target,
    context,
    states,
    *,
    publication_id=10,
    publication_revision=1,
):
    table = target.binding.publication_physical(publication_id, publication_revision)

    configuration_sha256 = (
        configuration_digest(
            context
        )
    )

    schema_sha256 = (
        model_adapter.schema_hash(
            context
        )
    )

    source_vector_sha256 = digest(
        [
            "published-source-vector",
            publication_id,
            publication_revision,
        ]
    )

    identity = (
        materialization_identity(
            workspace_id=(
                target.binding.workspace_id
            ),
            model_id=(
                target.binding.model_id
            ),
            model_version_id=(
                target.binding.version_id
            ),
            resource_id=(
                target.binding.resource_id
            ),
            generation=(
                target.binding.generation
            ),
            publication_id=(
                publication_id
            ),
            publication_revision=(
                publication_revision
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

    return ServingMaterializationRequest(
        binding=target.binding,
        publication_id=(
            publication_id
        ),
        publication_revision=(
            publication_revision
        ),
        materialization_token=(
            identity[
                "materialization_token"
            ]
        ),
        intent_sha256=(
            identity[
                "intent_sha256"
            ]
        ),
        configuration_sha256=(
            configuration_sha256
        ),
        schema_sha256=(
            schema_sha256
        ),
        source_vector_sha256=(
            source_vector_sha256
        ),
        physical_table=table,
        context=context,
        states=states,
    )


@pytest.mark.parametrize("kind", ["FACT", "DIMENSION"])
def test_complete_state_materializes_and_reconciles(kind):
    c = fact_context() if kind == "FACT" else dimension_context()
    states = compile_fact(c, [fact_evidence(c)]).after if kind == "FACT" else (dimension_first(c),)
    warehouse = target(c)
    request = serving_request(warehouse, c, states)
    result = warehouse.materialize_serving(request)
    assert model_adapter.states(c, warehouse.client.rows[request.physical_table]) == states
    assert result.serving_sha256 == request.serving_sha256 and result.row_count == request.row_count
    assert result.provider_job_id == warehouse._serving_job_id(request, "load")
    assert not warehouse.client.rows[warehouse._serving_table()]


def test_same_serving_request_is_idempotent():
    c = fact_context(); warehouse = target(c)
    request = serving_request(warehouse, c, compile_fact(c, [fact_evidence(c)]).after)
    assert warehouse.materialize_serving(request) == warehouse.materialize_serving(request)
    assert len([x for x in warehouse.client.calls if x[0] == "load"]) == 1
    assert not any("DELETE FROM" in x[1] for x in warehouse.client.calls if x[0] == "query")


def test_lost_load_response_recovers_without_second_write():
    c = fact_context(); warehouse = target(c)
    request = serving_request(warehouse, c, compile_fact(c, [fact_evidence(c)]).after)
    warehouse.client.lose_load_response = True
    with pytest.raises(ProviderError) as caught:
        warehouse.materialize_serving(request)
    assert caught.value.uncertain
    restarted = BigQueryTarget(warehouse.policy, warehouse.binding, warehouse.client)
    assert restarted.materialize_serving(request).serving_sha256 == request.serving_sha256
    assert len([x for x in warehouse.client.calls if x[0] == "load"]) == 1


def test_failed_load_never_writes_the_publication():
    c = fact_context(); warehouse = target(c)
    request = serving_request(warehouse, c, compile_fact(c, [fact_evidence(c)]).after)
    warehouse.client.fail_load = True
    with pytest.raises(ProviderError):
        warehouse.materialize_serving(request)
    assert not warehouse.client.rows[request.physical_table]


def test_later_publication_never_overwrites_prior_table():
    from copy import deepcopy
    c = fact_context(); warehouse = target(c)
    states = compile_fact(c, [fact_evidence(c)]).after
    first = serving_request(warehouse, c, states)
    warehouse.materialize_serving(first)
    snapshot = deepcopy(warehouse.client.rows[first.physical_table])
    second = serving_request(warehouse, c, states, publication_id=11, publication_revision=2)
    warehouse.materialize_serving(second)
    assert first.physical_table != second.physical_table
    assert warehouse.client.rows[first.physical_table] == snapshot
    assert warehouse.client.tables[first.physical_table].labels["publication"] == "10"
    assert warehouse.client.tables[second.physical_table].labels["revision"] == "2"


@pytest.mark.parametrize("drift", ["rows", "schema", "location", "publication", "revision", "partition", "cluster", "expiration", "type"])
def test_existing_publication_drift_fails_closed(drift):
    c = fact_context(); warehouse = target(c)
    request = serving_request(warehouse, c, compile_fact(c, [fact_evidence(c)]).after)
    warehouse.materialize_serving(request)
    table = warehouse.client.tables[request.physical_table]
    if drift == "rows": warehouse.client.rows[request.physical_table][0]["state_sha256"] = "f"*64
    if drift == "schema": table.schema = table.schema[:-1]
    if drift == "location": table._properties["location"] = "US"
    if drift in ("publication", "revision"): table.labels = table.labels | {drift: "99"}
    if drift == "partition": table._properties["timePartitioning"] = {"type": "DAY"}
    if drift == "cluster": table._properties["clustering"] = {"fields": ["fact_sk"]}
    if drift == "expiration": table._properties["expirationTime"] = "1900000000000"
    if drift == "type": table._properties["type"] = "VIEW"
    with pytest.raises(ProviderError): warehouse.materialize_serving(request)
    assert len([x for x in warehouse.client.calls if x[0] == "load"]) == 1


def test_cost_admission_blocks_readiness_and_payload_blocks_load():
    c = fact_context(); warehouse = target(c, maximum_bytes=200)
    request = serving_request(warehouse, c, compile_fact(c, [fact_evidence(c)]).after)
    warehouse.client.estimate = 201
    with pytest.raises(ProviderError, match="COST_LIMIT_EXCEEDED"): warehouse.materialize_serving(request)
    small = target(c, maximum_payload_bytes=1)
    request = serving_request(small, c, request.states)
    with pytest.raises(ProviderError, match="COST_LIMIT_EXCEEDED"): small.materialize_serving(request)
    assert not any(x[0] == "load" for x in small.client.calls)


def test_empty_snapshot_is_verified_without_fake_business_rows_or_load():
    c = fact_context(); warehouse = target(c)
    request = serving_request(warehouse, c, ())
    first = warehouse.materialize_serving(request)
    assert first == warehouse.materialize_serving(request)
    assert first.row_count == 0 and first.provider_job_id.startswith("empty_")
    assert not any(x[0] == "load" for x in warehouse.client.calls)


@pytest.mark.parametrize("drift", ["labels", "location", "destination", "project", "job_type"])
def test_lost_job_scope_is_independently_verified(drift):
    c = fact_context(); warehouse = target(c)
    request = serving_request(warehouse, c, compile_fact(c, [fact_evidence(c)]).after)
    warehouse.materialize_serving(request)
    job = warehouse.client.jobs[warehouse._serving_job_id(request, "load")]
    if drift == "labels": job.labels = {}
    if drift == "location": job.location = "US"
    if drift == "destination": job.destination = "synthetic-project.dr_dev_w1.m99_v99_g1"
    if drift == "project": job.project = "unrelated-project"
    if drift == "job_type": job.job_type = "query"
    with pytest.raises(ProviderError, match="JOB_CONFLICT"): warehouse.materialize_serving(request)
