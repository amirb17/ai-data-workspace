"""Version 2 exact-decimal state artifact contract.

No database access or publication. Existing V1 manifests are untouched.
"""

import hashlib
import json

import pandas as pd

from app.processing.append_engine import effective_schema
from app.processing.exact_parquet import (
    read_exact_parquet,
    write_exact_parquet,
)
from app.processing.exact_state_contract import (
    validate_exact_state_contract,
)


FORMAT_VERSION = 2


def descriptor_for(
    frame,
    rules,
    business_columns,
    *,
    dataset_version_id,
    policy_id,
    rule_version,
):
    if any(
        type(value) is not int or value <= 0
        for value in (
            dataset_version_id,
            policy_id,
            rule_version,
        )
    ):
        raise ValueError("Positive source pins required")

    # Pin the full approved rule configuration, not just
    # the exact numeric columns.
    normalized = []
    seen = set()

    for rule in rules:
        if (
            len(rule) < 5
            or rule[1] != dataset_version_id
        ):
            raise ValueError("Approved rule source differs")

        key = (rule[2], rule[3])

        if key in seen:
            raise ValueError("Duplicate approved rule")
        seen.add(key)

        normalized.append({
            "column_name": rule[2],
            "rule_type": rule[3],
            "rule_config": rule[4],
        })

    normalized.sort(
        key=lambda item: (
            item["column_name"],
            item["rule_type"],
        )
    )

    rule_bytes = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    exact = validate_exact_state_contract(
        frame,
        rules,
        business_columns,
    )

    exact_columns = {
        c["name"]: c
        for c in exact["exact_columns"]
    }

    schema = effective_schema(
        frame,
        business_columns,
    )

    for column in schema:
        specification = exact_columns.get(
            column["name"]
        )

        if specification is not None:
            column.update({
                "data_type": "DECIMAL_EXACT",
                "precision": specification["precision"],
                "scale": specification["scale"],
            })

    return {
        "manifest_version": FORMAT_VERSION,
        "dataset_version_id": dataset_version_id,
        "policy_id": policy_id,
        "rule_version": rule_version,
        "rules_sha256": hashlib.sha256(
            rule_bytes
        ).hexdigest(),
        "effective_schema": schema,
        "exact_contract": exact,
    }


def encode_exact_state(
    frame,
    rules,
    business_columns,
    *,
    dataset_version_id,
    policy_id,
    rule_version,
):
    descriptor = descriptor_for(
        frame,
        rules,
        business_columns,
        dataset_version_id=dataset_version_id,
        policy_id=policy_id,
        rule_version=rule_version,
    )

    payload, contract = write_exact_parquet(
        frame,
        rules,
        business_columns,
    )

    if contract != descriptor["exact_contract"]:
        raise ValueError(
            "Serialized exact contract differs"
        )

    return payload, descriptor


def decode_exact_state(
    payload,
    checksum,
    descriptor,
    rules,
    business_columns,
    *,
    dataset_version_id,
    policy_id,
    rule_version,
    expected_columns,
):
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("manifest_version") != FORMAT_VERSION
        or not isinstance(checksum, str)
        or hashlib.sha256(payload).hexdigest() != checksum
    ):
        raise ValueError(
            "Exact state artifact identity differs"
        )

    restored = read_exact_parquet(
        payload,
        descriptor["exact_contract"],
        rules,
        business_columns,
        expected_columns=expected_columns,
    )

    expected = descriptor_for(
        restored,
        rules,
        business_columns,
        dataset_version_id=dataset_version_id,
        policy_id=policy_id,
        rule_version=rule_version,
    )

    if descriptor != expected:
        raise ValueError(
            "Exact state descriptor differs from pinned rules"
        )

    return restored