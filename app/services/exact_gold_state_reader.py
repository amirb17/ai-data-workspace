"""Isolated, test-gated V2 trusted-state reader."""

import hashlib
import json
import os

from app.db.processing_repository import get_approved_policy
from app.processing.silver_transformer import exact_decimal_spec


def read_pinned_exact_state(
    store,
    state,
    application,
    policy,
):
    if (
        os.getenv("RUN_DB_TESTS") != "1"
        or os.getenv("DATARISE_EXACT_GOLD_V2_TEST_ONLY") != "1"
    ):
        raise ValueError(
            "Exact Gold state reading restricted to isolated tests"
        )

    if state["status"] != "PUBLISHED":
        raise ValueError("Trusted state is not published")

    for field in ("dataset_id", "dataset_version_id", "policy_id"):
        if application[field] != state[field]:
            raise ValueError("Exact state application ownership differs")

        if field != "dataset_id" and policy[field] != state[field]:
            raise ValueError("Exact state policy ownership differs")

    if application["application_id"] != state["source_application_id"]:
        raise ValueError("Exact state source application differs")

    approved_rules = get_approved_policy(
        state["dataset_version_id"],
        application["applied_rule_version"],
    )

    if not approved_rules or not any(
        rule[3] == "DATA_TYPE"
        and exact_decimal_spec(rule[4]) is not None
        for rule in approved_rules
    ):
        raise ValueError("Approved Exact Decimal rules required")

    body = store.read(state["manifest_key"])

    if hashlib.sha256(body).hexdigest() != state["manifest_sha256"]:
        raise ValueError("Trusted V2 state manifest integrity failed")

    manifest = json.loads(body)

    if manifest.get("manifest_version") != 2:
        raise ValueError("Trusted state must use manifest V2")

    for field in (
        "application_id",
        "dataset_id",
        "dataset_version_id",
        "policy_id",
    ):
        expected = (
            application["application_id"]
            if field == "application_id"
            else state[field]
        )

        if manifest.get(field) != expected:
            raise ValueError("Trusted V2 state manifest ownership differs")

    columns = [c["name"] for c in policy["schema_columns"]]

    # The existing V2 state validator checks immutable
    # Parquet, precise Arrow types, approved contracts,
    # source pins and row accounting.
    frame = store.validate_state(
        state["manifest_key"],
        state["manifest_sha256"],
        application,
        columns,
        state["row_count"],
        manifest["effective_schema"],
        policy=policy,
        approved_rules=approved_rules,
    )

    if len(frame) != state["row_count"]:
        raise ValueError("Trusted V2 state row count differs")

    return state, frame, approved_rules