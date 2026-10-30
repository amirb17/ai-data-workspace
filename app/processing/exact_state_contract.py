"""Versioned exact-decimal state evidence.

Pure validation only. The caller must authenticate the approved
rule snapshot and immutable source before using this contract.
"""

import hashlib
import json
from decimal import Decimal

import pandas as pd

from app.processing.exact_decimal import parse_exact_decimal
from app.processing.silver_transformer import exact_decimal_spec
from app.warehouse.providers.base import ProviderError
from app.warehouse.providers.types import decimal_type


CONTRACT_VERSION = 2


def validate_exact_state_contract(
    frame: pd.DataFrame,
    approved_rules: list,
    business_columns: list[str],
) -> dict:
    if (
        not business_columns
        or len(business_columns) != len(set(business_columns))
        or not frame.columns.is_unique
        or not set(business_columns).issubset(frame.columns)
    ):
        raise ValueError("Invalid state business schema")

    specs = {}

    for rule in approved_rules:
        if rule[3] != "DATA_TYPE":
            continue

        spec = exact_decimal_spec(rule[4])

        if spec is None:
            continue

        name = rule[2]

        if name not in business_columns or name in specs:
            raise ValueError(
                "Invalid exact decimal column contract"
            )

        specs[name] = spec

    if not specs:
        raise ValueError(
            "No approved exact decimal columns"
        )

    columns = []

    for name in sorted(specs):
        spec = specs[name]

        for value in frame[name].tolist():
            if value is None or value is pd.NA:
                continue

            if type(value) is not Decimal:
                raise ValueError(
                    "Exact state contains non-Decimal value"
                )

            verified = parse_exact_decimal(
                value,
                spec,
            )

            if (
                verified != value
                or verified.as_tuple().exponent != -spec.scale
            ):
                raise ValueError(
                    "Exact state scale differs from contract"
                )

            try:
                decimal_type(value)
            except ProviderError:
                raise ValueError(
                    "Exact value exceeds BigQuery numeric envelope"
                ) from None

        columns.append({
            "name": name,
            "logical_type": "DECIMAL_EXACT",
            "physical_type": "BIGNUMERIC",
            "precision": spec.precision,
            "scale": spec.scale,
        })

    contract = {
        "schema_version": CONTRACT_VERSION,
        "exact_columns": columns,
    }

    encoded = json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return {
        **contract,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }