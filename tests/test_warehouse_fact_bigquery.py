"""FACT execution through the bounded BigQuery mutation adapter."""

from decimal import Decimal

import pytest

from app.warehouse.fact_planning import (
    compile_fact,
)
from app.warehouse.providers.base import (
    Binding,
    Policy,
    ProviderError,
    TokenState,
)
from app.warehouse.providers.bigquery import (
    Batch,
    BigQueryTarget,
)
from tests.bigquery_fake import Client
from tests.test_warehouse_fact_planning import (
    context,
    evidence,
)


def target():
    binding = Binding(
        "synthetic-project",
        1,
        1,
        1,
        1,
        1,
    )

    cloud = Client()

    result = BigQueryTarget(
        Policy(
            "synthetic-project",
            enabled=True,
        ),
        binding,
        cloud,
    )

    return result, cloud


def batch(plan, target, operation=1):
    return Batch(
        target.binding,
        operation,
        f"{operation:064x}",
        f"{operation + 100:064x}",
        plan,
    )


def test_fact_bigquery_initial_apply_and_reconcile():
    c = context()

    target_instance, cloud = target()

    target_instance.provision(c)

    plan = compile_fact(
        c,
        [evidence(c)],
    )

    work = batch(
        plan,
        target_instance,
    )

    assert (
        target_instance.lookup(work)
        == TokenState.NOT_APPLIED
    )

    result = target_instance.apply(work)

    assert result == plan.result

    assert (
        target_instance.lookup(work)
        == TokenState.APPLIED
    )

    assert (
        target_instance.reconcile(work)
        == plan.result
    )

    assert (
        target_instance.last_reconciliation[
            "affected_facts"
        ]
        == 1
    )

    assert (
        target_instance.last_reconciliation[
            "inserted_facts"
        ]
        == 1
    )


def test_fact_bigquery_incremental_update_preserves_identity():
    c = context()

    target_instance, _ = target()

    target_instance.provision(c)

    first = compile_fact(
        c,
        [evidence(c)],
    )

    target_instance.apply(
        batch(
            first,
            target_instance,
            1,
        )
    )

    second = compile_fact(
        c,
        [
            evidence(
                c,
                2,
                outcome="UPDATED",
                amount=Decimal("25.75"),
            )
        ],
        first.after,
    )

    result = target_instance.apply(
        batch(
            second,
            target_instance,
            2,
        )
    )

    assert result == second.result

    assert (
        second.after[0].fact_sk
        == first.after[0].fact_sk
    )

    assert (
        target_instance.last_reconciliation[
            "updated_facts"
        ]
        == 1
    )


def test_fact_candidate_can_be_reconstructed_as_published_input():
    c = context()

    target_instance, _ = target()

    target_instance.provision(c)

    plan = compile_fact(
        c,
        [evidence(c)],
    )

    work = batch(
        plan,
        target_instance,
    )

    target_instance.apply(work)

    assert (
        target_instance.read_published_candidate(
            work,
            plan.result,
        )
        == plan.after
    )


def test_fact_lost_response_is_recovered_by_token_lookup():
    c = context()

    target_instance, cloud = target()

    target_instance.provision(c)

    work = batch(
        compile_fact(
            c,
            [evidence(c)],
        ),
        target_instance,
    )

    cloud.lose_response = True

    with pytest.raises(ProviderError):
        target_instance.apply(work)

    assert (
        target_instance.lookup(work)
        == TokenState.APPLIED
    )

    assert (
        target_instance.apply(work)
        == work.plan.result
    )