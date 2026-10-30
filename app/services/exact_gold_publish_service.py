"""Transactional, isolated-test-only V2 Gold publication.

Requires an already pinned, immutable and validated candidate.
Normal database migrations continue to prohibit V2 SUCCESS.
"""

import os
import re

from psycopg.rows import dict_row

from app.db.database import (
    get_connection,
    repository_transaction,
)
from app.services.processing_context_service import (
    validate_scope,
    lock_scope,
)
from app.services.dataset_analytics_service import refresh_lock
from app.services.exact_gold_candidate_service import (
    _load_pins,
    _require_test_gate,
)
from app.services.exact_gold_state_reader import (
    read_pinned_exact_state,
)
from app.services.exact_gold_avg_policy import (
    read_pinned_avg_policy,
    approved_rules_fingerprint,
)
from app.storage.exact_cumulative_gold_artifacts import (
    ExactCumulativeGoldArtifacts,
)


def publish_exact_gold_candidate(
    workspace_id,
    dataset_id,
    user,
    source_state_id,
    *,
    store=None,
):
    _require_test_gate()

    if os.getenv(
        "DATARISE_EXACT_GOLD_PUBLISH_TEST_ONLY"
    ) != "1":
        raise RuntimeError(
            "Exact Gold publication restricted to isolated tests"
        )

    if type(source_state_id) is not int or source_state_id <= 0:
        raise ValueError("Invalid Exact Gold source state")

    validate_scope(user, workspace_id, dataset_id)

    # Restrict this coordinator to the random schemas
    # created by tests/test_rule_approval_postgres.py.
    with get_connection() as conn:
        schema = conn.execute(
            "SELECT current_schema()"
        ).fetchone()[0]

    if not re.fullmatch(
        r"test_phase5a_[0-9a-f]{32}", schema
    ):
        raise RuntimeError(
            "Exact Gold publication requires isolated test schema"
        )

    artifact_store = (
        store if store is not None
        else ExactCumulativeGoldArtifacts()
    )

    if not isinstance(
        artifact_store, ExactCumulativeGoldArtifacts
    ):
        raise ValueError("Exact Gold V2 artifact store required")

    # Serialize analytics publication for this dataset.
    with refresh_lock(workspace_id, dataset_id):

        state, application, policy, run = _load_pins(
            workspace_id,
            dataset_id,
            user,
            source_state_id,
            allow_success=True,
        )

        approval = read_pinned_avg_policy(
            source_state_id
        )

        if (
            run["build_version"] != 2
            or run["exact_gold_approval_id"]
            != approval["approval_id"]
            or run["manifest_key"] is None
            or run["manifest_sha256"] is None
            or run["catalog"] is None
            or run["row_count"] != state["row_count"]
        ):
            raise ValueError(
                "Complete pinned Exact Gold candidate required"
            )

        # Verify actual published state bytes and
        # independently approved source rules.
        _, _, approved_rules = read_pinned_exact_state(
            artifact_store,
            state,
            application,
            policy,
        )

        rules_hash = approved_rules_fingerprint(
            approved_rules
        )

        if (
            rules_hash != approval["approved_rules_sha256"]
            or approval["source_policy_id"] != run["policy_id"]
        ):
            raise ValueError(
                "Pinned Exact Gold approval/source mismatch"
            )

        # Read immutable Gold Parquet and validate all
        # stored Decimal physical contracts.
        verified_catalog = artifact_store.validate_exact(
            run,
            run["manifest_key"],
            run["manifest_sha256"],
            state["row_count"],
            [
                col["name"]
                for col in policy["schema_columns"]
            ],
            approved_rules,
            approval["avg_scales"],
        )

        if verified_catalog != run["catalog"]:
            raise ValueError(
                "Pinned Exact Gold catalog differs"
            )

        # External storage validation is complete.
        # From this point, database publication is atomic.
        with repository_transaction() as conn:
            lock_scope(
                conn,
                user,
                workspace_id,
                dataset_id,
            )

            cursor = conn.cursor(row_factory=dict_row)

            head = cursor.execute(
                """
                SELECT current_state_id,
                       current_gold_run_id,
                       state_analytics_status
                FROM datasets
                WHERE dataset_id = %s
                  AND workspace_id = %s
                """,
                (dataset_id, workspace_id),
            ).fetchone()

            if (
                head is None
                or head["current_state_id"]
                != source_state_id
            ):
                raise ValueError(
                    "Exact Gold source head changed"
                )

            locked_state = cursor.execute(
                """
                SELECT *
                FROM dataset_state_versions
                WHERE state_id = %s
                  AND dataset_id = %s
                  AND status = 'PUBLISHED'
                FOR SHARE
                """,
                (source_state_id, dataset_id),
            ).fetchone()

            locked_run = cursor.execute(
                """
                SELECT *
                FROM dataset_gold_runs
                WHERE gold_run_id = %s
                  AND dataset_id = %s
                  AND source_state_id = %s
                  AND build_version = 2
                FOR UPDATE
                """,
                (
                    run["gold_run_id"],
                    dataset_id,
                    source_state_id,
                ),
            ).fetchone()

            if (
                locked_state is None
                or locked_run is None
                or locked_state["manifest_sha256"]
                != state["manifest_sha256"]
                or locked_run["source_sha256"]
                != state["manifest_sha256"]
                or locked_run["dataset_version_id"]
                != state["dataset_version_id"]
                or locked_run["policy_id"]
                != state["policy_id"]
                or locked_run["exact_gold_approval_id"]
                != approval["approval_id"]
                or locked_run["manifest_key"]
                != run["manifest_key"]
                or locked_run["manifest_sha256"]
                != run["manifest_sha256"]
                or locked_run["catalog"]
                != verified_catalog
                or locked_run["row_count"]
                != state["row_count"]
            ):
                raise ValueError(
                    "Exact Gold candidate changed before publication"
                )

            locked_approval = cursor.execute(
                """
                SELECT *
                FROM exact_gold_avg_policies
                WHERE approval_id = %s
                  AND source_state_id = %s
                FOR SHARE
                """,
                (
                    approval["approval_id"],
                    source_state_id,
                ),
            ).fetchone()

            if (
                locked_approval is None
                or locked_approval["approved_rules_sha256"]
                != rules_hash
                or locked_approval["avg_scales"]
                != approval["avg_scales"]
                or locked_approval["source_policy_id"]
                != run["policy_id"]
            ):
                raise ValueError(
                    "Exact Gold approval changed before publication"
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
                    locked_approval["applied_rule_version"],
                ),
            ).fetchone()

            if snapshot is None:
                raise ValueError(
                    "Approved rules snapshot unavailable"
                )

            locked_rules = [
                (
                    None,
                    state["dataset_version_id"],
                    item["column_name"],
                    item["rule_type"],
                    item["rule_config"],
                    True,
                )
                for item in snapshot["rules"]
            ]

            if (
                approved_rules_fingerprint(locked_rules)
                != rules_hash
            ):
                raise ValueError(
                    "Approved rules changed before publication"
                )

            if locked_run["status"] == "SUCCESS":
                if (
                    head["current_gold_run_id"]
                    != locked_run["gold_run_id"]
                    or head["state_analytics_status"]
                    != "FRESH"
                ):
                    raise ValueError(
                        "Published Exact Gold pointer differs"
                    )

                reused = True

            elif locked_run["status"] == "REFRESHING":

                # This transaction-local setting is honored
                # ONLY by the isolated test-schema guard
                # installed in Step 3 below.
                cursor.execute(
                    """
                    SELECT set_config(
                        'datarise.exact_gold_v2_test_publish',
                        '1',
                        true
                    )
                    """
                )

                updated = cursor.execute(
                    """
                    UPDATE dataset_gold_runs
                    SET status = 'SUCCESS',
                        completed_at = NOW(),
                        failure_code = NULL
                    WHERE gold_run_id = %s
                      AND status = 'REFRESHING'
                      AND build_version = 2
                    RETURNING gold_run_id
                    """,
                    (run["gold_run_id"],),
                ).fetchone()

                if updated is None:
                    raise ValueError(
                        "Exact Gold publication transition failed"
                    )

                published_head = cursor.execute(
                    """
                    UPDATE datasets
                    SET current_gold_run_id = %s,
                        state_analytics_status = 'FRESH'
                    WHERE dataset_id = %s
                      AND workspace_id = %s
                      AND current_state_id = %s
                    RETURNING dataset_id
                    """,
                    (
                        run["gold_run_id"],
                        dataset_id,
                        workspace_id,
                        source_state_id,
                    ),
                ).fetchone()

                if published_head is None:
                    raise ValueError(
                        "Exact Gold publication head changed"
                    )

                reused = False

            else:
                raise ValueError(
                    "Exact Gold run is not publishable"
                )

    return {
        "status": "PUBLISHED",
        "source_state_id": source_state_id,
        "gold_run_id": run["gold_run_id"],
        "approval_id": approval["approval_id"],
        "reused": reused,
        "published": True,
    }