"""Pure provider-neutral FACT logical planning."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID
import json
import pytest
from dataclasses import replace
from app.schemas.warehouse_modeling import FactConfiguration
from app.schemas.warehouse_modeling import (
    FactConfiguration,
    SourceSchema,
)
from app.warehouse.accepted_changes import (
    SourcePin,
    digest,
    event,
    manifest,
    projection,
)
from app.warehouse.fact_planning import (
    FactContext,
    compile_fact,
    fact_state_digest,
)
from app.warehouse.keys import surrogate_key
from app.warehouse.scd_types import SourceContractPin


NOW = datetime(
    2026,
    1,
    1,
    tzinfo=timezone.utc,
)

NS = UUID(
    "00000000-0000-0000-0000-000000000001"
)


def context():
    configuration = FactConfiguration(
        model_type="FACT",
        grain=dict(
            description="One row per order line",
            identity_columns=("id", "line"),
        ),
        measures=("amount",),
        attributes=("segment",),
        event_time=dict(
            column="effective",
            interpretation="UTC",
        ),
    )

    schema = SourceSchema(
        columns=(
            dict(
                name="id",
                data_type="STRING",
                nullable=False,
            ),
            dict(
                name="line",
                data_type="INTEGER",
                nullable=False,
            ),
            dict(
                name="amount",
                data_type="DECIMAL",
                nullable=False,
            ),
            dict(
                name="segment",
                data_type="STRING",
                nullable=True,
            ),
            dict(
                name="effective",
                data_type="TIMESTAMP",
                nullable=False,
            ),
        )
    )

    return FactContext(
        model_id=1,
        model_version_id=1,
        namespace=NS,
        configuration=configuration,
        source_schema=schema,
        source_contract=SourceContractPin(
            1,
            1,
            1,
            1,
        ),
    )


def evidence(
    c,
    position=1,
    *,
    outcome="INSERTED",
    amount=Decimal("10.50"),
    segment="Retail",
    effective=NOW,
    evidence_key=None,
):
    pin = SourcePin(
        workspace_id=1,
        dataset_id=1,
        dataset_version_id=1,
        policy_id=1,
        rule_version=1,
        rule_sha256="a" * 64,
        application_id=position,
        upload_id=position,
        source_dq_run_id=position,
        state_id=position,
        previous_state_id=(
            position - 1
            if position > 1
            else None
        ),
        artifact_sha256="b" * 64,
        strategy="UPSERT",
        accepted_at=NOW,
    )

    row = dict(
        id="O001",
        line=1,
        amount=amount,
        segment=segment,
        effective=effective,
    )

    accepted = event(
        pin,
        evidence_key
        or f"ledger-key:{position}",
        outcome,
        row,
        projection(c.configuration),
    )
    configuration_sha256 = (
        c.configuration_sha256
        or digest(
            c.configuration.model_dump(
                mode="json"
            )
        )
    )

    return manifest(
        pin,
        1,
        c.model_version_id,
        configuration_sha256,
        [accepted],
        position,
    )

def test_insert_has_stable_fact_identity_and_exact_values():
    c = context()

    plan = compile_fact(
        c,
        [evidence(c)],
    )

    assert len(plan.after) == 1

    state = plan.after[0]

    assert state.fact_sk == surrogate_key(
        NS,
        "FACT",
        ("id", "line"),
        {
            "id": "O001",
            "line": 1,
        },
    )

    assert dict(state.identity) == {
        "id": "O001",
        "line": 1,
    }

    assert dict(state.measures) == {
        "amount": Decimal("10.5"),
    }

    assert dict(state.attributes) == {
        "segment": "Retail",
    }

    assert state.event_time == NOW
    assert state.active is True

    assert plan.result.inserted == 1
    assert plan.result.events == 1
    assert plan.result.manifests == 1


def test_update_preserves_fact_sk_and_revises_business_values():
    c = context()

    first = compile_fact(
        c,
        [evidence(c)],
    )

    second = compile_fact(
        c,
        [
            evidence(
                c,
                2,
                outcome="UPDATED",
                amount=Decimal("15.75"),
                segment="Enterprise",
            )
        ],
        first.after,
    )

    before = first.after[0]
    after = second.after[0]

    assert after.fact_sk == before.fact_sk

    assert dict(after.measures) == {
        "amount": Decimal("15.75"),
    }

    assert dict(after.attributes) == {
        "segment": "Enterprise",
    }

    assert after.source_position == 2
    assert second.result.updated == 1


def test_unchanged_advances_provenance_without_business_change():
    c = context()

    first = compile_fact(
        c,
        [evidence(c)],
    )

    second = compile_fact(
        c,
        [
            evidence(
                c,
                2,
                outcome="UNCHANGED",
            )
        ],
        first.after,
    )

    assert (
        second.after[0].fact_sk
        == first.after[0].fact_sk
    )

    assert (
        second.after[0].measures
        == first.after[0].measures
    )

    assert (
        second.after[0].attributes
        == first.after[0].attributes
    )

    assert second.after[0].source_position == 2
    assert second.result.unchanged == 1


def test_deactivate_and_reactivate_preserve_identity():
    c = context()

    first = compile_fact(
        c,
        [evidence(c)],
    )

    inactive = compile_fact(
        c,
        [
            evidence(
                c,
                2,
                outcome="DEACTIVATED",
            )
        ],
        first.after,
    )

    assert inactive.after[0].active is False

    active = compile_fact(
        c,
        [
            evidence(
                c,
                3,
                outcome="REACTIVATED",
                amount=Decimal("20.00"),
            )
        ],
        inactive.after,
    )

    assert active.after[0].active is True

    assert (
        active.after[0].fact_sk
        == first.after[0].fact_sk
    )

    assert dict(active.after[0].measures) == {
        "amount": Decimal("20"),
    }


def test_exact_retry_is_idempotent():
    c = context()
    accepted = evidence(c)

    first = compile_fact(
        c,
        [accepted],
    )

    retry = compile_fact(
        c,
        [accepted],
        first.after,
    )

    assert retry.after == first.after

    assert (
        fact_state_digest(retry.after[0])
        == fact_state_digest(first.after[0])
    )


def test_same_fact_twice_in_manifest_is_rejected():
    c = context()

    base = evidence(c)
    pin = SourcePin.model_validate_json(
    json.dumps(base["pin"])
)

    first = event(
        pin,
        "first",
        "INSERTED",
        dict(
            id="O001",
            line=1,
            amount=Decimal("10"),
            segment="Retail",
            effective=NOW,
        ),
        projection(c.configuration),
    )

    second = event(
        pin,
        "second",
        "UPDATED",
        dict(
            id="O001",
            line=1,
            amount=Decimal("20"),
            segment="Retail",
            effective=NOW,
        ),
        projection(c.configuration),
    )

    body = manifest(
        pin,
        1,
        1,
        digest(
            c.configuration.model_dump(
                mode="json"
            )
        ),
        [first, second],
        1,
    )

    with pytest.raises(
        ValueError,
        match="intra-manifest",
    ):
        compile_fact(c, [body])


def test_insert_over_existing_identity_is_rejected():
    c = context()

    first = compile_fact(
        c,
        [evidence(c)],
    )

    with pytest.raises(
        ValueError,
        match="insert conflicts",
    ):
        compile_fact(
            c,
            [
                evidence(
                    c,
                    2,
                    outcome="INSERTED",
                )
            ],
            first.after,
        )


def test_unchanged_with_changed_payload_is_rejected():
    c = context()

    first = compile_fact(
        c,
        [evidence(c)],
    )

    with pytest.raises(
        ValueError,
        match="unchanged evidence",
    ):
        compile_fact(
            c,
            [
                evidence(
                    c,
                    2,
                    outcome="UNCHANGED",
                    amount=Decimal("99"),
                )
            ],
            first.after,
        )


def test_manifest_order_is_deterministic():
    c = context()

    first = evidence(c, 1)

    second = evidence(
        c,
        2,
        outcome="UPDATED",
        amount=Decimal("20"),
    )

    ordered = compile_fact(
        c,
        [first, second],
    )

    reversed_input = compile_fact(
        c,
        [second, first],
    )

    assert reversed_input == ordered


def test_null_fact_identity_fails_closed():
    c = context()

    pin = SourcePin(
        workspace_id=1,
        dataset_id=1,
        dataset_version_id=1,
        policy_id=1,
        rule_version=1,
        rule_sha256="a" * 64,
        application_id=1,
        upload_id=1,
        source_dq_run_id=1,
        state_id=1,
        artifact_sha256="b" * 64,
        strategy="UPSERT",
        accepted_at=NOW,
    )

    accepted = event(
        pin,
        "null-key",
        "INSERTED",
        dict(
            id=None,
            line=1,
            amount=Decimal("10"),
            segment="Retail",
            effective=NOW,
        ),
        projection(c.configuration),
    )

    body = manifest(
        pin,
        1,
        1,
        digest(
            c.configuration.model_dump(
                mode="json"
            )
        ),
        [accepted],
        1,
    )

    with pytest.raises(ValueError):
        compile_fact(
            c,
            [body],
        )
def test_legacy_fact_configuration_hash_is_preserved():
    current = context()

    legacy_raw = {
        "schema_version": 1,
        "model_type": "FACT",
        "grain": {
            "description": "One row per business event",
            "identity_columns": [
                "id",
                "line",
            ],
        },
        "event_time": {
            "column": "effective",
            "interpretation": "UTC",
        },
        "identity_null_policy": "REJECT",
    }

    legacy = FactContext(
        current.model_id,
        current.model_version_id,
        current.namespace,
        FactConfiguration.model_validate(
            legacy_raw
        ),
        current.source_schema,
        current.source_contract,
        configuration_sha256=digest(
            legacy_raw
        ),
    )

    assert legacy.configuration.measures == ()
    assert legacy.configuration.attributes == ()

    assert (
        digest(
            legacy.configuration.model_dump(
                mode="json"
            )
        )
        != legacy.configuration_sha256
    )

    plan = compile_fact(
        legacy,
        [
            evidence(
                legacy,
            )
        ],
    )

    assert len(plan.after) == 1

    assert dict(
        plan.after[0].identity
    ) == {
        "id": "O001",
        "line": 1,
    }

    assert plan.after[0].measures == ()
    assert plan.after[0].attributes == ()

def test_fact_configuration_hash_mismatch_is_rejected():
    c = context()

    evidence_manifest = evidence(c)

    changed = replace(
        c,
        configuration_sha256="f" * 64,
    )

    with pytest.raises(
        ValueError,
        match="source/model contract mismatch",
    ):
        compile_fact(
            changed,
            [evidence_manifest],
        )