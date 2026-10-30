"""Authenticated, test-gated Exact Gold AVG approval.

One immutable approval per current published V2 state.
No Gold publication occurs here.
"""

import os

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.db.database import repository_transaction
from app.services.processing_context_service import (
    validate_scope,
    lock_scope,
)
from app.services.exact_gold_state_reader import (
    read_pinned_exact_state,
)
from app.services.exact_gold_avg_policy import (
    approved_rules_fingerprint,
    validate_avg_scales,
)
from app.storage.incremental_artifacts import IncrementalArtifacts


def approve_exact_gold_avg_policy(
    workspace_id,
    dataset_id,
    user,
    source_state_id,
    avg_scales,
    expected_rules_sha256,
    *,
    store=None,
):
    if (
        os.getenv("RUN_DB_TESTS") != "1"
        or os.getenv("DATARISE_EXACT_GOLD_V2_TEST_ONLY") != "1"
        or os.getenv("DATARISE_EXACT_GOLD_POLICY_TEST_ONLY") != "1"
    ):
        raise RuntimeError(
            "Exact Gold AVG approval is not enabled"
        )

    if type(source_state_id) is not int or source_state_id <= 0:
        raise ValueError("Invalid source state")

    validate_scope(user, workspace_id, dataset_id)

    with repository_transaction() as conn:
        lock_scope(
            conn, user, workspace_id, dataset_id
        )

        cursor = conn.cursor(row_factory=dict_row)

        state = cursor.execute(
            """
            SELECT s.*
            FROM dataset_state_versions s
            JOIN datasets d
              ON d.dataset_id = s.dataset_id
            WHERE s.state_id = %s
              AND d.dataset_id = %s
              AND d.workspace_id = %s
              AND d.current_state_id = s.state_id
              AND s.status = 'PUBLISHED'
            FOR SHARE OF s
            """,
            (source_state_id, dataset_id, workspace_id),
        ).fetchone()

        if state is None:
            raise ValueError(
                "Current published source state required"
            )

        application = cursor.execute(
            """
            SELECT *
            FROM delivery_applications
            WHERE application_id = %s
            FOR SHARE
            """,
            (state["source_application_id"],),
        ).fetchone()

        policy = cursor.execute(
            """
            SELECT *
            FROM dataset_load_policies
            WHERE policy_id = %s
            FOR SHARE
            """,
            (state["policy_id"],),
        ).fetchone()

        if (
            application is None
            or policy is None
            or policy["load_strategy"] != "APPEND"
        ):
            raise ValueError(
                "Approved Exact APPEND policy required"
            )

        snapshot = cursor.execute(
            """
            SELECT rules
            FROM approved_rule_policies
            WHERE dataset_version_id = %s
              AND rule_version = %s
            FOR SHARE
            """,
            (
                state["dataset_version_id"],
                application["applied_rule_version"],
            ),
        ).fetchone()

        if snapshot is None:
            raise ValueError(
                "Approved source rule snapshot required"
            )

        actual_store = (
            store if store is not None
            else IncrementalArtifacts()
        )

        # Verify the actual published V2 state and
        # obtain its approved source rules.
        _, _, approved_rules = read_pinned_exact_state(
            actual_store,
            state,
            application,
            policy,
        )

        locked_rules = [
            (
                None,
                state["dataset_version_id"],
                rule["column_name"],
                rule["rule_type"],
                rule["rule_config"],
                True,
            )
            for rule in snapshot["rules"]
        ]

        if approved_rules != locked_rules:
            raise ValueError(
                "Approved source rules changed during review"
            )

        rules_hash = approved_rules_fingerprint(
            approved_rules
        )

        if expected_rules_sha256 != rules_hash:
            raise ValueError(
                "Approved source rule fingerprint changed"
            )

        scales = validate_avg_scales(
            approved_rules,
            avg_scales,
        )

        existing = cursor.execute(
            """
            SELECT *
            FROM exact_gold_avg_policies
            WHERE source_state_id = %s
            FOR SHARE
            """,
            (source_state_id,),
        ).fetchone()

        if existing is not None:
            if (
                existing["approved_rules_sha256"]
                != rules_hash
                or existing["avg_scales"] != scales
            ):
                raise ValueError(
                    "Source state already approved "
                    "with a different AVG policy"
                )

            return {
                "approval_id": existing["approval_id"],
                "source_state_id": source_state_id,
                "policy_version": existing["policy_version"],
                "avg_scales": scales,
                "already_approved": True,
            }

        approval = cursor.execute(
            """
            INSERT INTO exact_gold_avg_policies (
                source_state_id,
                source_application_id,
                dataset_id,
                dataset_version_id,
                source_policy_id,
                applied_rule_version,
                avg_scales,
                approved_rules_sha256,
                approved_by
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING approval_id, policy_version
            """,
            (
                state["state_id"],
                application["application_id"],
                state["dataset_id"],
                state["dataset_version_id"],
                state["policy_id"],
                application["applied_rule_version"],
                Jsonb(scales),
                rules_hash,
                user["user_id"],
            ),
        ).fetchone()

        return {
            "approval_id": approval["approval_id"],
            "source_state_id": source_state_id,
            "policy_version": approval["policy_version"],
            "avg_scales": scales,
            "already_approved": False,
        }