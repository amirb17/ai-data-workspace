"""Test-gated immutable V2 Exact Decimal cumulative Gold artifacts.

Does not publish PostgreSQL metadata or change the live Gold
refresh coordinator. AVG scales are test inputs, not an
approved production policy.
"""

import hashlib
import json
import os
from uuid import uuid4

from app.processing.exact_gold_contracts import (
    plan_exact_gold_contracts,
)
from app.processing.exact_gold_parquet import (
    write_exact_gold_parquet,
    read_exact_gold_parquet,
)
from app.storage.cumulative_gold_artifacts import (
    CumulativeGoldArtifacts,
)


def _require_test_gate():
    if (
        os.getenv("RUN_DB_TESTS") != "1"
        or os.getenv("DATARISE_EXACT_GOLD_V2_TEST_ONLY") != "1"
    ):
        raise ValueError(
            "Exact Gold V2 is restricted to isolated tests"
        )


class ExactCumulativeGoldArtifacts(CumulativeGoldArtifacts):

    def build_exact(
        self,
        run,
        artifacts,
        approved_rules,
        avg_scales,
    ):
        _require_test_gate()

        plan = plan_exact_gold_contracts(
            artifacts,
            approved_rules,
            avg_scales,
        )

        prefix = (
            f"gold/dataset_id={run['dataset_id']}/"
            f"state_id={run['source_state_id']}/"
            f"run_id={run['gold_run_id']}/"
            f"exact-v2-{uuid4().hex}/"
        )

        catalog = []

        for index, (frame, metadata) in enumerate(artifacts):
            artifact_name = metadata["artifact_name"]
            contracts = plan["artifacts"][artifact_name]

            key = prefix + f"artifact-{index}.parquet"

            payload, descriptor = write_exact_gold_parquet(
                frame,
                contracts,
            )

            checksum = self.put(key, payload)

            if checksum != descriptor["sha256"]:
                raise ValueError(
                    "Exact Gold artifact checksum differs"
                )

            # Verify the actual stored object, not only
            # the in-memory serialization result.
            read_exact_gold_parquet(
                self.read(key),
                descriptor,
            )

            catalog.append({
                **metadata,
                "gold_artifact_id": index + 1,
                "gold_run_id": run["gold_run_id"],
                "dataset_id": run["dataset_id"],
                "source_state_id": run["source_state_id"],
                "storage_path": key,
                "sha256": checksum,
                "row_count": len(frame),
                "dependencies": [{
                    "dataset_id": run["dataset_id"],
                    "state_id": run["source_state_id"],
                }],
                "exact_parquet": descriptor,
            })

        manifest = {
            "gold_format_version": 2,
            "build_version": run["build_version"],
            "dataset_id": run["dataset_id"],
            "source_state_id": run["source_state_id"],
            "source_sha256": run["source_sha256"],
            "dataset_version_id": run["dataset_version_id"],
            "policy_id": run["policy_id"],
            "gold_run_id": run["gold_run_id"],
            "gold_contracts": plan,
            "artifacts": catalog,
        }

        manifest_key = prefix + "manifest.json"
        checksum = self.put(
            manifest_key,
            self.json(manifest),
        )

        return manifest_key, checksum, catalog

    def validate_exact(
        self,
        run,
        key,
        checksum,
        expected_rows,
        columns,
        approved_rules,
        avg_scales,
    ):
        _require_test_gate()

        prefix = (
            f"gold/dataset_id={run['dataset_id']}/"
            f"state_id={run['source_state_id']}/"
            f"run_id={run['gold_run_id']}/"
        )

        if not key.startswith(prefix):
            raise ValueError(
                "Exact Gold manifest outside owned run"
            )

        body = self.read(key)

        if hashlib.sha256(body).hexdigest() != checksum:
            raise ValueError(
                "Exact Gold manifest integrity failed"
            )

        manifest = json.loads(body)

        if manifest.get("gold_format_version") != 2:
            raise ValueError(
                "Exact Gold V2 manifest required"
            )

        for field in (
            "build_version",
            "dataset_id",
            "source_state_id",
            "source_sha256",
            "dataset_version_id",
            "policy_id",
            "gold_run_id",
        ):
            if manifest.get(field) != run[field]:
                raise ValueError(
                    "Exact Gold source ownership differs"
                )

        catalog = manifest.get("artifacts")

        if (
            not isinstance(catalog, list)
            or not catalog
            or catalog[0].get("artifact_type") != "BASE"
            or catalog[0].get("row_count") != expected_rows
        ):
            raise ValueError(
                "Exact Gold BASE contract differs"
            )

        names = [
            item["artifact_name"]
            for item in catalog
        ]

        if len(names) != len(set(names)):
            raise ValueError(
                "Duplicate Exact Gold artifact identity"
            )

        candidate_prefix = key.rsplit("/", 1)[0] + "/"
        reconstructed = []

        for index, artifact in enumerate(catalog):
            artifact_key = artifact["storage_path"]

            expected_dependencies = [{
                "dataset_id": run["dataset_id"],
                "state_id": run["source_state_id"],
            }]

            if (
                not artifact_key.startswith(candidate_prefix)
                or artifact["dataset_id"] != run["dataset_id"]
                or artifact["source_state_id"]
                != run["source_state_id"]
                or artifact["gold_run_id"]
                != run["gold_run_id"]
                or artifact["gold_artifact_id"] != index + 1
                or artifact["dependencies"]
                != expected_dependencies
                or artifact["artifact_type"]
                != ("BASE" if index == 0 else "MART")
            ):
                raise ValueError(
                    "Exact Gold artifact ownership differs"
                )

            descriptor = artifact["exact_parquet"]

            if descriptor.get("sha256") != artifact["sha256"]:
                raise ValueError(
                    "Exact Gold descriptor checksum differs"
                )

            frame = read_exact_gold_parquet(
                self.read(artifact_key),
                descriptor,
            )

            semantic = artifact["columns"]

            if (
                len(frame) != artifact["row_count"]
                or list(frame.columns)
                != [c["column_name"] for c in semantic]
                or [str(frame[c].dtype) for c in frame]
                != [c["data_type"] for c in semantic]
                or not artifact["grain"]
            ):
                raise ValueError(
                    "Exact Gold catalog/schema differs"
                )

            if index == 0:
                if list(frame.columns) != columns:
                    raise ValueError(
                        "Exact Gold business columns differ"
                    )
            else:
                if (
                    "record_count" not in frame
                    or frame["record_count"].isna().any()
                    or not all(
                        type(v) is int or (
                            hasattr(v, "item")
                            and type(v.item()) is int
                        )
                        for v in frame["record_count"]
                    )
                    or (frame["record_count"] < 0).any()
                    or int(frame["record_count"].sum())
                    != expected_rows
                ):
                    raise ValueError(
                        "Exact Gold mart counts do not reconcile"
                    )

            reconstructed.append((frame, artifact))

        # Reconstruct the contract from independently supplied
        # approved rules and AVG policy, not merely the
        # self-described contracts in the stored manifest.
        verified_plan = plan_exact_gold_contracts(
            reconstructed,
            approved_rules,
            avg_scales,
        )

        if manifest["gold_contracts"] != verified_plan:
            raise ValueError(
                "Exact Gold approved contracts differ"
            )

        for artifact in catalog:
            expected_contract = verified_plan["artifacts"][
                artifact["artifact_name"]
            ]

            if (
                artifact["exact_parquet"]["exact_columns"]
                != expected_contract
            ):
                raise ValueError(
                    "Exact Gold physical contract differs"
                )

        return catalog