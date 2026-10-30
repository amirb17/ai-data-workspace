from decimal import Decimal

import pandas as pd
import pytest

from app.processing.cumulative_gold import build_cumulative_gold
from app.processing.exact_gold_contracts import (
    plan_exact_gold_contracts,
)
from app.processing.exact_gold_parquet import (
    write_exact_gold_parquet,
    read_exact_gold_parquet,
)


RULES = [
    (
        None,
        39,
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


POLICY = {
    "load_strategy": "APPEND",
    "schema_columns": [
        {"name": "order_id"},
        {"name": "category"},
        {"name": "amount"},
    ],
    "business_keys": [],
}


def artifacts_for(values):
    frame = pd.DataFrame({
        "order_id": [
            f"O{i:03d}"
            for i in range(len(values))
        ],
        "category": ["retail"] * len(values),
        "amount": values,
    })

    return build_cumulative_gold(frame, POLICY)


def test_source_and_derived_contracts_are_explicit():
    artifacts = artifacts_for([
        Decimal("0.1000"),
        Decimal("1234567890123456.0001"),
    ])

    planned = plan_exact_gold_contracts(
        artifacts,
        RULES,
        {"amount": 5},
    )

    assert planned["contract_version"] == 2

    assert planned["artifacts"]["base"]["amount"] == {
        "precision": 20,
        "scale": 4,
    }

    summary = planned["artifacts"]["category_summary"]

    assert summary == {
        "amount_sum": {
            "precision": 21,
            "scale": 4,
        },
        "amount_mean": {
            "precision": 21,
            "scale": 5,
        },
        "amount_min": {
            "precision": 20,
            "scale": 4,
        },
        "amount_max": {
            "precision": 20,
            "scale": 4,
        },
    }


def test_all_gold_artifacts_round_trip_exactly():
    artifacts = artifacts_for([
        Decimal("0.1000"),
        Decimal("1234567890123456.0001"),
    ])

    planned = plan_exact_gold_contracts(
        artifacts,
        RULES,
        {"amount": 5},
    )

    for frame, metadata in artifacts:
        contract = planned["artifacts"][
            metadata["artifact_name"]
        ]

        payload, descriptor = write_exact_gold_parquet(
            frame,
            contract,
        )

        restored = read_exact_gold_parquet(
            payload,
            descriptor,
        )

        for column in contract:
            assert restored[column].tolist() == (
                frame[column].tolist()
            )


def test_missing_or_narrow_average_scale_rejected():
    artifacts = artifacts_for([
        Decimal("1.0000"),
        Decimal("2.0000"),
    ])

    with pytest.raises(
        ValueError,
        match="AVG scale",
    ):
        plan_exact_gold_contracts(
            artifacts,
            RULES,
            {},
        )

    with pytest.raises(
        ValueError,
        match="AVG scale",
    ):
        plan_exact_gold_contracts(
            artifacts,
            RULES,
            {"amount": 3},
        )


def test_sum_precision_accounts_for_row_bound():
    artifacts = artifacts_for([
        Decimal("9999999999999999.9999")
        for _ in range(100)
    ])

    planned = plan_exact_gold_contracts(
        artifacts,
        RULES,
        {"amount": 5},
    )

    assert planned["artifacts"]["category_summary"][
        "amount_sum"
    ] == {
        "precision": 22,
        "scale": 4,
    }