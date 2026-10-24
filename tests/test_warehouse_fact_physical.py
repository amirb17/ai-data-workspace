"""Exact provider-neutral FACT physical representation."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from app.warehouse.fact_planning import (
    compile_fact,
)
from app.warehouse.providers.base import (
    ProviderError,
)
from app.warehouse.providers import fact_physical
from tests.test_warehouse_fact_planning import (
    NOW,
    context,
    evidence,
)


def test_fact_schema_has_stable_business_mapping():
    c = context()

    mapped = fact_physical.columns(c)

    assert mapped == (
        ("amount", "c0", "BIGNUMERIC"),
        ("effective", "c1", "TIMESTAMP"),
        ("id", "c2", "STRING"),
        ("line", "c3", "INT64"),
        ("segment", "c4", "STRING"),
    )

    fields = {
        name: (kind, mode)
        for name, kind, mode
        in fact_physical.schema(c)
    }

    assert fields["fact_sk"] == (
        "STRING",
        "REQUIRED",
    )

    assert fields["c2"] == (
        "STRING",
        "REQUIRED",
    )

    assert fields["c3"] == (
        "INT64",
        "REQUIRED",
    )

    assert fields["c0"] == (
        "BIGNUMERIC",
        "NULLABLE",
    )


def test_fact_physical_round_trip_is_exact():
    c = context()

    plan = compile_fact(
        c,
        [evidence(c)],
    )

    encoded = fact_physical.rows(plan)
    decoded = fact_physical.states(
        c,
        encoded,
    )

    assert decoded == plan.after

    mapping = {
        logical: physical
        for logical, physical, _
        in fact_physical.columns(c)
    }

    row = encoded[0]

    assert row[mapping["amount"]] == "10.5"
    assert row[mapping["line"]] == "1"

    assert row[mapping["effective"]] == (
        NOW.isoformat(
            timespec="microseconds"
        )
    )


def test_null_fact_attribute_round_trip_is_exact():
    c = context()

    plan = compile_fact(
        c,
        [
            evidence(
                c,
                segment=None,
            )
        ],
    )

    encoded = fact_physical.rows(plan)

    mapping = {
        logical: physical
        for logical, physical, _
        in fact_physical.columns(c)
    }

    assert (
        encoded[0][mapping["segment"]]
        is None
    )

    assert (
        fact_physical.states(
            c,
            encoded,
        )
        == plan.after
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "identity",
        "measure",
        "fact_sk",
        "state_digest",
        "event_checksum",
        "extra_field",
        "missing_field",
    ],
)
def test_fact_physical_corruption_fails_closed(
    corruption,
):
    c = context()

    plan = compile_fact(
        c,
        [evidence(c)],
    )

    encoded = deepcopy(
        fact_physical.rows(plan)
    )

    row = encoded[0]

    mapping = {
        logical: physical
        for logical, physical, _
        in fact_physical.columns(c)
    }

    if corruption == "identity":
        row[mapping["id"]] = "OTHER"

    elif corruption == "measure":
        row[mapping["amount"]] = "99"

    elif corruption == "fact_sk":
        row["fact_sk"] = "c" * 64

    elif corruption == "state_digest":
        row["state_sha256"] = "c" * 64

    elif corruption == "event_checksum":
        row["last_event_checksum"] = "c" * 64

    elif corruption == "extra_field":
        row["unexpected"] = "value"

    else:
        del row[mapping["segment"]]

    with pytest.raises(ProviderError):
        fact_physical.states(
            c,
            encoded,
        )


def test_duplicate_physical_fact_identity_is_rejected():
    c = context()

    plan = compile_fact(
        c,
        [evidence(c)],
    )

    encoded = fact_physical.rows(plan)

    with pytest.raises(ProviderError):
        fact_physical.states(
            c,
            encoded + deepcopy(encoded),
        )


def test_physical_encoder_rejects_out_of_range_source_position():
    c = context()

    plan = compile_fact(
        c,
        [evidence(c)],
    )

    invalid = replace(
        plan.after[0],
        source_position=1 << 63,
    )

    changed = replace(
        plan,
        after=(invalid,),
    )

    with pytest.raises(ProviderError):
        fact_physical.rows(changed)