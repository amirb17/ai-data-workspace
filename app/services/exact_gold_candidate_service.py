"""Test-only Exact Gold V2 candidate preparation.

Builds and validates immutable Gold artifacts from a
published Exact Decimal state.

Does NOT publish Gold metadata or update dataset pointers.
"""

import os
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row

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
    read_pinned_avg_policy,
)
from app.processing.cumulative_gold import (
    build_cumulative_gold,
)
from app.storage.exact_cumulative_gold_artifacts import (
    ExactCumulativeGoldArtifacts,
)


def _require_test_gate():
    required = (
        "RUN_DB_TESTS",
        "DATARISE_EXACT_GOLD_V2_TEST_ONLY",
        "DATARISE_EXACT_GOLD_POLICY_TEST_ONLY",
    )

    if any(os.getenv(name) != "1" for name in required):
        raise RuntimeError(
            "Exact Gold V2 candidate preparation "
            "is restricted to isolated tests"
        )


def _load_pins(
    workspace_id,
    dataset_id,
    user,
    source_state_id,
    *,
    allow_success=False
):
    """Read owned, published source and prepared V2 Gold run."""

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
            SELECT current_state_id
            FROM datasets
            WHERE workspace_id = %s
              AND dataset_id = %s
            """,
            (workspace_id, dataset_id),
        ).fetchone()

        if (
            head is None
            or head["current_state_id"] != source_state_id
        ):
            raise ValueError(
                "Current published source state required"
            )

        state = cursor.execute(
            """
            SELECT *
            FROM dataset_state_versions
            WHERE state_id = %s
              AND dataset_id = %s
              AND status = 'PUBLISHED'
            """,
            (source_state_id, dataset_id),
        ).fetchone()

        if state is None:
            raise ValueError(
                "Published Exact state unavailable"
            )

        application = cursor.execute(
            """
            SELECT *
            FROM delivery_applications
            WHERE application_id = %s
              AND dataset_id = %s
            """,
            (
                state["source_application_id"],
                dataset_id,
            ),
        ).fetchone()

        policy = cursor.execute(
            """
            SELECT *
            FROM dataset_load_policies
            WHERE policy_id = %s
              AND dataset_id = %s
            """,
            (state["policy_id"], dataset_id),
        ).fetchone()

        run = cursor.execute(
            """
            SELECT *
            FROM dataset_gold_runs
            WHERE dataset_id = %s
              AND source_state_id = %s
              AND build_version = 2
            """,
            (dataset_id, source_state_id),
        ).fetchone()

        if (
            application is None
            or policy is None
            or run is None
            or policy["load_strategy"] != "APPEND"
            or run["status"] not in (
                {"REFRESHING", "SUCCESS"}
                if allow_success
                else {"REFRESHING"}
            )
            or run["exact_gold_approval_id"] is None
        ):
            raise ValueError(
                "Prepared Exact Gold V2 run required"
            )

        if (
            run["dataset_version_id"]
            != state["dataset_version_id"]
            or run["policy_id"] != state["policy_id"]
            or run["source_sha256"]
            != state["manifest_sha256"]
            or application["dataset_version_id"]
            != state["dataset_version_id"]
            or application["policy_id"] != state["policy_id"]
            or policy["dataset_version_id"]
            != state["dataset_version_id"]
        ):
            raise ValueError(
                "Exact Gold source pins differ"
            )

        return state, application, policy, run


def prepare_exact_gold_candidate(
    workspace_id,
    dataset_id,
    user,
    source_state_id,
    *,
    store=None,
):
    _require_test_gate()

    if type(source_state_id) is not int or source_state_id <= 0:
        raise ValueError("Invalid source state")

    validate_scope(
        user,
        workspace_id,
        dataset_id,
    )

    state, application, policy, run = _load_pins(
        workspace_id,
        dataset_id,
        user,
        source_state_id,
    )

    artifact_store = (
        store
        if store is not None
        else ExactCumulativeGoldArtifacts()
    )

    if not isinstance(
        artifact_store,
        ExactCumulativeGoldArtifacts,
    ):
        raise ValueError(
            "Exact Gold V2 artifact store required"
        )

    # Read the real V2 published state and approved rules.
    _, frame, approved_rules = read_pinned_exact_state(
        artifact_store,
        state,
        application,
        policy,
    )

    # Independently retrieve the persisted AVG approval.
    approval = read_pinned_avg_policy(source_state_id)

    if (
        approval["approval_id"]
        != run["exact_gold_approval_id"]
        or approval["source_policy_id"]
        != run["policy_id"]
        or approval["approved_rules_sha256"]
        != approved_rules_fingerprint(approved_rules)
    ):
        raise ValueError(
            "Exact Gold AVG approval differs from run"
        )

    # Derive Gold artifacts from authoritative current state.
    artifacts = build_cumulative_gold(
        frame,
        policy,
    )

    if len(artifacts[0][0]) != state["row_count"]:
        raise ValueError(
            "Exact Gold BASE row count differs"
        )

    avg_scales = approval["avg_scales"]

    # Immutable candidate objects; no PostgreSQL publication.
    key, checksum, catalog = artifact_store.build_exact(
        run,
        artifacts,
        approved_rules,
        avg_scales,
    )

    verified = artifact_store.validate_exact(
        run,
        key,
        checksum,
        state["row_count"],
        [
            column["name"]
            for column in policy["schema_columns"]
        ],
        approved_rules,
        avg_scales,
    )

    if verified != catalog:
        raise ValueError(
            "Exact Gold candidate catalog differs"
        )

    # Recheck ownership and head after external storage IO.
    latest_state, _, _, latest_run = _load_pins(
        workspace_id,
        dataset_id,
        user,
        source_state_id,
    )

    if (
        latest_state["state_id"] != state["state_id"]
        or latest_state["manifest_sha256"]
        != state["manifest_sha256"]
        or latest_run["gold_run_id"]
        != run["gold_run_id"]
        or latest_run["exact_gold_approval_id"]
        != run["exact_gold_approval_id"]
    ):
        raise ValueError(
            "Exact Gold source changed during preparation"
        )

    # Internal test result only; never expose storage keys
    # through a public API.
    return {
        "status": "VALIDATED_NOT_PUBLISHED",
        "source_state_id": source_state_id,
        "gold_run_id": run["gold_run_id"],
        "approval_id": approval["approval_id"],
        "manifest_key": key,
        "manifest_sha256": checksum,
        "artifact_count": len(catalog),
        "row_count": len(artifacts[0][0]),
        "catalog": catalog,
        "published": False,
    }
def pin_exact_gold_candidate(
    workspace_id,
    dataset_id,
    user,
    source_state_id,
    candidate,
    *,
    store=None,
):
    """Pin a validated V2 candidate without publishing Gold."""

    _require_test_gate()

    if (
        type(source_state_id) is not int
        or source_state_id <= 0
        or not isinstance(candidate, dict)
        or candidate.get("status") != "VALIDATED_NOT_PUBLISHED"
        or candidate.get("published") is not False
    ):
        raise ValueError("Invalid Exact Gold candidate")

    validate_scope(user, workspace_id, dataset_id)

    state, application, policy, run = _load_pins(
        workspace_id,
        dataset_id,
        user,
        source_state_id,
    )

    approval = read_pinned_avg_policy(source_state_id)

    if (
        candidate.get("source_state_id") != source_state_id
        or candidate.get("gold_run_id") != run["gold_run_id"]
        or candidate.get("approval_id") != approval["approval_id"]
        or run["exact_gold_approval_id"] != approval["approval_id"]
        or candidate.get("row_count") != state["row_count"]
    ):
        raise ValueError("Exact Gold candidate pins differ")

    artifact_store = (
        store
        if store is not None
        else ExactCumulativeGoldArtifacts()
    )

    if not isinstance(
        artifact_store,
        ExactCumulativeGoldArtifacts,
    ):
        raise ValueError("Exact Gold artifact store required")

    # Recheck real state and approved source rules.
    _, _, approved_rules = read_pinned_exact_state(
        artifact_store,
        state,
        application,
        policy,
    )

    if (
        approved_rules_fingerprint(approved_rules)
        != approval["approved_rules_sha256"]
    ):
        raise ValueError("Approved Exact Gold rules differ")

    # Verify the stored candidate bytes again before pinning.
    catalog = artifact_store.validate_exact(
        run,
        candidate["manifest_key"],
        candidate["manifest_sha256"],
        state["row_count"],
        [
            column["name"]
            for column in policy["schema_columns"]
        ],
        approved_rules,
        approval["avg_scales"],
    )

    if catalog != candidate["catalog"]:
        raise ValueError("Exact Gold candidate catalog differs")

    # External S3 validation is complete.
    # Recheck the database pins under locks before writing.
    with repository_transaction() as conn:
        lock_scope(conn, user, workspace_id, dataset_id)

        cursor = conn.cursor(row_factory=dict_row)

        head = cursor.execute(
            """
            SELECT current_state_id
            FROM datasets
            WHERE dataset_id = %s
              AND workspace_id = %s
            """,
            (dataset_id, workspace_id),
        ).fetchone()

        if (
            head is None
            or head["current_state_id"] != source_state_id
        ):
            raise ValueError(
                "Exact Gold candidate source is stale"
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

        pinned_run = cursor.execute(
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
            or pinned_run is None
            or pinned_run["status"] != "REFRESHING"
            or locked_state["manifest_sha256"]
            != state["manifest_sha256"]
            or pinned_run["source_sha256"]
            != state["manifest_sha256"]
            or pinned_run["policy_id"] != state["policy_id"]
            or pinned_run["dataset_version_id"]
            != state["dataset_version_id"]
            or pinned_run["exact_gold_approval_id"]
            != approval["approval_id"]
        ):
            raise ValueError("Exact Gold run source changed")

        locked_approval = cursor.execute(
            """
            SELECT *
            FROM exact_gold_avg_policies
            WHERE approval_id = %s
              AND source_state_id = %s
            FOR SHARE
            """,
            (approval["approval_id"], source_state_id),
        ).fetchone()

        if (
            locked_approval is None
            or locked_approval["source_policy_id"]
            != pinned_run["policy_id"]
            or locked_approval["approved_rules_sha256"]
            != approval["approved_rules_sha256"]
            or locked_approval["avg_scales"]
            != approval["avg_scales"]
        ):
            raise ValueError("Exact Gold approval changed")

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
            raise ValueError("Approved rule snapshot unavailable")

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

        if (
            approved_rules_fingerprint(locked_rules)
            != approval["approved_rules_sha256"]
        ):
            raise ValueError("Approved rule snapshot changed")

        fields = (
            "manifest_key",
            "manifest_sha256",
            "catalog",
            "row_count",
        )

        if pinned_run["manifest_key"] is not None:
            if (
                pinned_run["manifest_key"]
                != candidate["manifest_key"]
                or pinned_run["manifest_sha256"]
                != candidate["manifest_sha256"]
                or pinned_run["catalog"] != catalog
                or pinned_run["row_count"]
                != state["row_count"]
            ):
                raise ValueError(
                    "Different Exact Gold candidate already pinned"
                )

            reused = True

        else:
            if any(
                pinned_run[field] is not None
                for field in fields
            ):
                raise ValueError(
                    "Incomplete Exact Gold candidate pin"
                )

            cursor.execute(
                """
                UPDATE dataset_gold_runs
                SET
                    manifest_key = %s,
                    manifest_sha256 = %s,
                    catalog = %s,
                    row_count = %s
                WHERE gold_run_id = %s
                  AND status = 'REFRESHING'
                  AND build_version = 2
                """,
                (
                    candidate["manifest_key"],
                    candidate["manifest_sha256"],
                    Jsonb(catalog),
                    state["row_count"],
                    run["gold_run_id"],
                ),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    "Exact Gold candidate pin failed"
                )

            reused = False

    return {
        "status": "PINNED_NOT_PUBLISHED",
        "gold_run_id": run["gold_run_id"],
        "source_state_id": source_state_id,
        "approval_id": approval["approval_id"],
        "reused": reused,
        "published": False,
    }