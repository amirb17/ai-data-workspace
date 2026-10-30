"""Read pinned Exact Gold AVG policies.

Approval insertion is not exposed by this module.
Production activation is deliberately unavailable.
"""

import hashlib
import json
import os

from psycopg.rows import dict_row

from app.db.database import get_connection
from app.db.processing_repository import get_approved_policy
from app.processing.silver_transformer import exact_decimal_spec


def approved_rules_fingerprint(rules):
    normalized = []

    for rule in rules:
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

    keys = [
        (item["column_name"], item["rule_type"])
        for item in normalized
    ]

    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate approved rule identity")

    payload = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()


def validate_avg_scales(approved_rules, avg_scales):
    sources = {}

    for rule in approved_rules:
        if rule[3] != "DATA_TYPE":
            continue

        spec = exact_decimal_spec(rule[4])

        if spec is not None:
            if rule[2] in sources:
                raise ValueError(
                    "Duplicate Exact Decimal source"
                )

            sources[rule[2]] = spec

    if not sources:
        raise ValueError(
            "No approved Exact Decimal sources"
        )

    if (
        not isinstance(avg_scales, dict)
        or set(avg_scales) != set(sources)
    ):
        raise ValueError(
            "Exact Gold AVG scales must match approved sources"
        )

    for column, spec in sources.items():
        scale = avg_scales[column]

        if (
            type(scale) is not int
            or scale < spec.scale
            or spec.precision - spec.scale + scale > 76
        ):
            raise ValueError(
                "Invalid approved Exact Gold AVG scale"
            )

    return dict(avg_scales)


def read_pinned_avg_policy(source_state_id):
    if (
        os.getenv("RUN_DB_TESTS") != "1"
        or os.getenv(
            "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY"
        ) != "1"
    ):
        raise ValueError(
            "Exact Gold AVG policy reader restricted "
            "to isolated tests"
        )

    with get_connection() as conn:
        row = conn.cursor(row_factory=dict_row).execute(
            """
            SELECT
                p.*,
                s.status AS source_state_status,
                a.applied_rule_version AS application_rule_version
            FROM exact_gold_avg_policies p
            JOIN dataset_state_versions s
              ON s.state_id = p.source_state_id
            JOIN delivery_applications a
              ON a.application_id = s.source_application_id
            WHERE p.source_state_id = %s
              AND s.dataset_id = p.dataset_id
              AND s.dataset_version_id = p.dataset_version_id
              AND s.policy_id = p.source_policy_id
              AND s.source_application_id = p.source_application_id
            """,
            (source_state_id,),
        ).fetchone()

    if row is None:
        raise ValueError(
            "Pinned Exact Gold AVG approval unavailable"
        )

    if (
        row["source_state_status"] != "PUBLISHED"
        or row["application_rule_version"]
        != row["applied_rule_version"]
        or row["policy_version"] != 1
    ):
        raise ValueError(
            "Exact Gold AVG approval source differs"
        )

    approved_rules = get_approved_policy(
        row["dataset_version_id"],
        row["applied_rule_version"],
    )

    if (
        approved_rules_fingerprint(approved_rules)
        != row["approved_rules_sha256"]
    ):
        raise ValueError(
            "Exact Gold approved rule snapshot differs"
        )

    scales = validate_avg_scales(
        approved_rules,
        row["avg_scales"],
    )

    return {
        "approval_id": row["approval_id"],
        "source_state_id": row["source_state_id"],
        "source_policy_id": row["source_policy_id"],
        "policy_version": row["policy_version"],
        "approved_rules_sha256": row["approved_rules_sha256"],
        "avg_scales": scales,
        "approved_rules": approved_rules,
    }