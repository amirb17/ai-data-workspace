from dataclasses import replace

import pytest

from app.warehouse.accepted_changes import (
    digest,
)
from app.warehouse.fact_planning import (
    compile_fact,
)
from app.warehouse.providers import (
    fact_physical,
    model_adapter,
    physical,
)
from app.warehouse.providers.base import (
    Binding,
    ProviderError,
)
from app.warehouse.serving_materialization import (
    ServingMaterializationRequest,
    configuration_digest,
    materialization_identity,
)

from tests.test_warehouse_fact_planning import (
    context as fact_context,
    evidence as fact_evidence,
)
from tests.test_warehouse_scd import (
    context as dimension_context,
    first as dimension_first,
)


def request(
    context,
    states,
    **updates,
):
    binding = Binding(
        "synthetic-project",
        1,
        context.model_id,
        context.model_version_id,
        1,
        1,
    )

    table = binding.publication_physical(10, 2)

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
            "test-source-vector",
            1,
        ]
    )

    identity = (
        materialization_identity(
            workspace_id=(
                binding.workspace_id
            ),
            model_id=(
                binding.model_id
            ),
            model_version_id=(
                binding.version_id
            ),
            resource_id=(
                binding.resource_id
            ),
            generation=(
                binding.generation
            ),
            publication_id=10,
            publication_revision=2,
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

    values = dict(
        binding=binding,
        publication_id=10,
        publication_revision=2,
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

    values.update(
        updates
    )

    return ServingMaterializationRequest(
        **values
    )


def test_complete_fact_state_has_exact_physical_round_trip():
    context = fact_context()

    plan = compile_fact(
        context,
        [
            fact_evidence(
                context
            ),
        ],
    )

    rows = (
        model_adapter
        .rows_from_states(
            context,
            plan.after,
        )
    )

    assert rows == (
        fact_physical.rows(
            plan
        )
    )

    assert (
        model_adapter.states(
            context,
            rows,
        )
        == plan.after
    )


def test_inactive_facts_and_precise_measures_remain_in_complete_snapshot():
    from decimal import Decimal
    c = fact_context()
    precise = Decimal('12345678901234567890.123456789012345678')
    initial = compile_fact(c, [fact_evidence(c, amount=precise, segment='東京')]).after
    inactive = compile_fact(c, [fact_evidence(c, 2, outcome='DEACTIVATED', amount=precise, segment='東京')], initial).after
    snapshot = request(c, inactive)
    assert snapshot.row_count == 1
    decoded = model_adapter.states(c, snapshot.physical_rows)
    assert decoded == inactive and decoded[0].active is False
    assert dict(decoded[0].measures)['amount'] == precise


def test_persisted_legacy_fact_configuration_hash_is_authoritative():
    c = replace(fact_context(), configuration_sha256='f'*64)
    states = compile_fact(c, [fact_evidence(c)]).after
    snapshot = request(c, states)
    assert snapshot.configuration_sha256 == 'f'*64
    assert snapshot.configuration_sha256 != digest(c.configuration.model_dump(mode='json'))


def test_duplicate_complete_dimension_members_fail_with_provider_error():
    c = dimension_context(); member = dimension_first(c)
    with pytest.raises(ProviderError): request(c, (member, member))


def test_complete_dimension_state_has_exact_physical_round_trip():
    context = (
        dimension_context()
    )

    states = (
        dimension_first(
            context
        ),
    )

    rows = (
        model_adapter
        .rows_from_states(
            context,
            states,
        )
    )

    assert rows == (
        physical.rows_from_states(
            context,
            states,
        )
    )

    assert (
        model_adapter.states(
            context,
            rows,
        )
        == states
    )


def test_serving_request_has_deterministic_rows_and_checksum():
    context = fact_context()

    plan = compile_fact(
        context,
        [
            fact_evidence(
                context
            ),
        ],
    )

    first = request(
        context,
        plan.after,
    )

    second = request(
        context,
        plan.after,
    )

    assert (
        first.physical_rows
        == second.physical_rows
    )

    assert (
        first.serving_sha256
        == second.serving_sha256
    )

    assert first.row_count == 1


def test_serving_request_rejects_wrong_registered_table():
    context = fact_context()

    plan = compile_fact(
        context,
        [
            fact_evidence(
                context
            ),
        ],
    )

    with pytest.raises(
        ProviderError,
    ):
        request(
            context,
            plan.after,
            physical_table=(
                "synthetic-project."
                "dr_dev_w1."
                "attacker_table"
            ),
        )


def test_serving_request_rejects_wrong_schema_hash():
    context = fact_context()

    plan = compile_fact(
        context,
        [
            fact_evidence(
                context
            ),
        ],
    )

    with pytest.raises(
        ProviderError,
    ):
        request(
            context,
            plan.after,
            schema_sha256=(
                "f" * 64
            ),
        )


def test_serving_request_rejects_modified_identity():
    context = fact_context()

    plan = compile_fact(
        context,
        [
            fact_evidence(
                context
            ),
        ],
    )

    with pytest.raises(
        ProviderError,
    ):
        request(
            context,
            plan.after,
            materialization_token=(
                "e" * 64
            ),
        )


def test_serving_request_requires_deterministic_state_order():
    context = dimension_context()

    first = dimension_first(
        context
    )

    altered = replace(
        first,
        member_sk=(
            "f" * 64
        ),
    )

    # The physical decoder independently reconstructs member identity,
    # so caller-supplied corrupted state cannot become a serving request.
    with pytest.raises(
        ProviderError,
    ):
        request(
            context,
            (
                altered,
            ),
        )
