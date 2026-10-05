from app.processing.gold_catalog_builder import build_gold_artifact_catalog
from app.processing.gold_processor import process_gold
from app.processing.silver_processor import process_silver
from app.processing.bronze_processor import run_bronze_stage

from app.db.file_repository import (
    get_active_processing_attempt_for_dataset_version_file,
    get_dataset_profiles,
    get_latest_successful_dq_run_for_dataset_version_file,
    get_next_attempt_number_for_dataset_version_file,
    get_successful_gold_run_for_dq_run_and_dataset_version_file,

    # Physical Bronze-scoped functions
    get_physical_file_by_id,
    get_next_attempt_number,
    get_active_processing_attempt,
    update_physical_file_status,

    # Shared processing persistence
    create_processing_attempt,
    complete_processing_attempt,
    save_data_quality_run,
    save_gold_run,
    save_dataset_profiles,
    save_dataset_profile_summary,
    save_data_quality_issue,
    update_physical_file_schema_hash,
    get_upload_request_by_id,
    save_gold_artifact,
    get_gold_artifacts_for_run,
    save_gold_artifact_model,
    save_gold_artifact_column,
    get_gold_artifact_model,
    get_gold_artifact_columns,
)

from app.db.dataset_repository import (
    assign_physical_file_to_dataset_version,
    get_dataset_version_file_by_id,
    update_dataset_version_file_status,
    initialize_dataset_version_file_rules,
    get_dataset_version_rule_version,
)

from app.services.dataset_service import (
    resolve_dataset_version_by_id,
)
from app.processing.gold_planner import (
    build_gold_plan,
)
from app.storage.s3_service import parse_s3_uri

def start_processing(upload_id: int):
    upload_request = get_upload_request_by_id(upload_id)

    if upload_request is None:
        raise ValueError(
            f"Upload request {upload_id} not found"
        )

    file_id = upload_request[2]
    workspace_id = upload_request[3]
    dataset_id = upload_request[4]

    if workspace_id is None or dataset_id is None:
        raise ValueError(
            f"Upload request {upload_id} is missing "
            "workspace/dataset context"
        )

    physical_file = get_physical_file_by_id(file_id)

    if physical_file is None:
        raise ValueError("Physical file not found")

    storage_path = physical_file[4]
    schema_hash = physical_file[6]

    if not storage_path:
        raise ValueError(
            "Physical file does not have a storage path"
        )

    # ---------------------------------------------------------
    # CASE 1:
    # Physical file has already completed Bronze previously.
    # Reuse its physical/schema metadata instead of running
    # Bronze again for another workspace/dataset context.
    # ---------------------------------------------------------
    if schema_hash:
        dataset_version = resolve_dataset_version_by_id(
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            schema_hash=schema_hash,
        )

        dataset_version_file = (
            assign_physical_file_to_dataset_version(
                file_id=file_id,
                dataset_version_id=dataset_version[
                    "dataset_version_id"
                ],
            )
        )

        if dataset_version_file is None:
            raise RuntimeError(
                "Failed to resolve dataset-version-file association"
            )

        dataset_version_file = (
            initialize_dataset_version_file_rules(
                dataset_version_file_id=dataset_version_file[0],
            )
        )

        return {
            "file": physical_file,
            "attempt": None,
            "bronze": {
                "reused": True,
                "schema_hash": schema_hash,
            },
            "upload_id": upload_id,
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "dataset_version_file": dataset_version_file,
        }

    # ---------------------------------------------------------
    # CASE 2:
    # Physical file has never completed Bronze.
    # Run physical Bronze processing once.
    # ---------------------------------------------------------
    active_attempt = get_active_processing_attempt(file_id)

    if active_attempt:
        raise RuntimeError(
            f"File is already being processed "
            f"in attempt {active_attempt[2]}"
        )

    attempt_number = get_next_attempt_number(file_id)

    attempt = create_processing_attempt(
        file_id=file_id,
        attempt_number=attempt_number,
        stage="BRONZE",
        status="PROCESSING",
    )

    attempt_id = attempt[0]

    raw_object_key = parse_s3_uri(storage_path)

    try:
        bronze_result = run_bronze_stage(
            file_id=file_id,
            raw_object_key=raw_object_key,
        )

        update_physical_file_schema_hash(
            file_id=file_id,
            schema_hash=bronze_result["schema_hash"],
        )

        save_dataset_profiles(
            file_id=file_id,
            profiles=bronze_result["profiles"],
        )

        save_dataset_profile_summary(
            file_id=file_id,
            total_rows=bronze_result["row_count"],
            total_columns=len(
                bronze_result["column_names"]
            ),
        )

        dataset_version = resolve_dataset_version_by_id(
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            schema_hash=bronze_result["schema_hash"],
        )

        dataset_version_file = (
            assign_physical_file_to_dataset_version(
                file_id=file_id,
                dataset_version_id=dataset_version[
                    "dataset_version_id"
                ],
            )
        )

        if dataset_version_file is None:
            raise RuntimeError(
                "Failed to resolve dataset-version-file association"
            )

        dataset_version_file = (
            initialize_dataset_version_file_rules(
                dataset_version_file_id=dataset_version_file[0],
            )
        )

        completed_attempt = complete_processing_attempt(
            attempt_id=attempt_id,
            status="SUCCESS",
        )

        # Physical file remains physically available.
        # AWAITING_RULES belongs to dataset_version_files,
        # not to the shared physical file.
        update_physical_file_status(
            file_id=file_id,
            status="UPLOADED",
        )

        return {
            "file": get_physical_file_by_id(file_id),
            "attempt": completed_attempt,
            "bronze": {
                **bronze_result,
                "reused": False,
            },
            "upload_id": upload_id,
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "dataset_version_file": dataset_version_file,
        }

    except Exception as exc:
        complete_processing_attempt(
            attempt_id=attempt_id,
            status="FAILED",
            error_message=str(exc),
        )

        update_physical_file_status(
            file_id=file_id,
            status="FAILED",
        )

        raise RuntimeError(
            f"Bronze processing failed: {exc}"
        )
def run_silver_processing(
    dataset_version_file_id: int,
    bucket_name: str,
):
    # ---------------------------------------------------------
    # 1. Resolve dataset-version-file context
    # ---------------------------------------------------------
    context = get_dataset_version_file_by_id(
        dataset_version_file_id
    )

    if context is None:
        raise ValueError(
            f"Dataset-version-file "
            f"{dataset_version_file_id} not found"
        )

    dataset_version_id = context[1]
    file_id = context[2]
    status = context[3]

    # ---------------------------------------------------------
    # 2. Verify physical file exists
    # ---------------------------------------------------------
    physical_file = get_physical_file_by_id(file_id)

    if physical_file is None:
        raise ValueError(
            f"Physical file {file_id} not found"
        )

    # ---------------------------------------------------------
    # 3. Get rule version for THIS dataset version
    # ---------------------------------------------------------
    current_rule_version = (
        get_dataset_version_rule_version(
            dataset_version_id
        )
    )

    # ---------------------------------------------------------
    # 4. SILVER IDEMPOTENCY CHECK
    #
    # Check Silver history for THIS dataset-version-file,
    # not globally for the shared physical file.
    # ---------------------------------------------------------
    latest_dq_run = (
        get_latest_successful_dq_run_for_dataset_version_file(
            dataset_version_file_id
        )
    )

    if latest_dq_run is not None:
        silver_rule_version = latest_dq_run[3]

        if silver_rule_version == current_rule_version:
            return {
                "dataset_version_file_id":
                    dataset_version_file_id,
                "dataset_version_id":
                    dataset_version_id,
                "file_id": file_id,
                "stage": "SILVER",
                "status": "SUCCESS",
                "already_processed": True,
                "attempt_id": latest_dq_run[2],
                "dq_run_id": latest_dq_run[0],
                "rule_version": silver_rule_version,
                "total_rows": latest_dq_run[4],
                "valid_rows": latest_dq_run[5],
                "rejected_rows": latest_dq_run[6],
                "silver_path": latest_dq_run[7],
                "quarantine_path": latest_dq_run[8],
            }

    # ---------------------------------------------------------
    # 5. Validate DVF lifecycle
    # ---------------------------------------------------------
    if status not in (
        "READY_FOR_SILVER",
        "SILVER_FAILED",
        "READY_FOR_GOLD",
        "SUCCESS",
    ):
        raise ValueError(
            "Silver processing cannot start. "
            f"Current dataset-version-file status: {status}"
        )

    # ---------------------------------------------------------
    # 6. Get next attempt number
    #
    # Attempt numbering is still file-based for now.
    # The attempt itself is scoped using DVF ID.
    # ---------------------------------------------------------
    attempt_number = (
    get_next_attempt_number_for_dataset_version_file(
        dataset_version_file_id
    )
)

    # ---------------------------------------------------------
    # 7. Create SILVER attempt with DVF lineage
    # ---------------------------------------------------------
    attempt = create_processing_attempt(
        file_id=file_id,
        attempt_number=attempt_number,
        stage="SILVER",
        status="PROCESSING",
        dataset_version_file_id=dataset_version_file_id,
    )

    attempt_id = attempt[0]

    # ---------------------------------------------------------
    # 8. Mark only THIS DVF as processing
    # ---------------------------------------------------------
    update_dataset_version_file_status(
        dataset_version_file_id=
            dataset_version_file_id,
        status="SILVER_PROCESSING",
    )

    try:
        # -----------------------------------------------------
        # 9. Run Silver
        # -----------------------------------------------------
        result = process_silver(
            file_id=file_id,
            dataset_version_id=dataset_version_id,
            rule_version=current_rule_version,
            bucket_name=bucket_name,
        )

        # -----------------------------------------------------
        # 10. Persist DQ run with DVF lineage
        # -----------------------------------------------------
        dq_run = save_data_quality_run(
            file_id=file_id,
            attempt_id=attempt_id,
            rule_version=current_rule_version,
            total_rows=result["total_rows"],
            valid_rows=result["valid_rows"],
            rejected_rows=result["rejected_rows"],
            silver_path=result["silver_key"],
            quarantine_path=result["quarantine_key"],
            dataset_version_file_id=
                dataset_version_file_id,
        )

        dq_run_id = dq_run[0]

        # -----------------------------------------------------
        # 11. Persist DQ issues
        # -----------------------------------------------------
        for issue in result["dq_issues"]:
            save_data_quality_issue(
                dq_run_id=dq_run_id,
                column_name=issue["column_name"],
                rule_type=issue["rule_type"],
                violation_count=
                    issue["violation_count"],
            )

        # -----------------------------------------------------
        # 12. Complete attempt
        # -----------------------------------------------------
        complete_processing_attempt(
            attempt_id=attempt_id,
            status="SUCCESS",
        )

        # -----------------------------------------------------
        # 13. This DVF is now ready for Gold
        # -----------------------------------------------------
        update_dataset_version_file_status(
            dataset_version_file_id=
                dataset_version_file_id,
            status="READY_FOR_GOLD",
        )

        return {
            "dataset_version_file_id":
                dataset_version_file_id,
            "dataset_version_id":
                dataset_version_id,
            "file_id": file_id,
            "stage": "SILVER",
            "status": "SUCCESS",
            "already_processed": False,
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "dq_run_id": dq_run_id,
            "rule_version": current_rule_version,
            "total_rows": result["total_rows"],
            "valid_rows": result["valid_rows"],
            "rejected_rows": result["rejected_rows"],
            "silver_path": result["silver_key"],
            "quarantine_path":
                result["quarantine_key"],
        }

    except Exception as exc:
        complete_processing_attempt(
            attempt_id=attempt_id,
            status="FAILED",
            error_message=str(exc),
        )

        # IMPORTANT:
        # Do not mark physical_files FAILED.
        # Other datasets/users may share that physical file.
        update_dataset_version_file_status(
            dataset_version_file_id=
                dataset_version_file_id,
            status="SILVER_FAILED",
        )

        raise RuntimeError(
            f"Silver processing failed: {exc}"
        ) from exc

def _get_expected_gold_artifact_names(
    file_id: int,
) -> set[str]:
    profiles = get_dataset_profiles(file_id)

    if not profiles:
        raise ValueError(
            f"No dataset profiles found for file_id={file_id}"
        )

    gold_plan = build_gold_plan(profiles)

    return {
        "base",
        *[
            artifact.artifact_name
            for artifact in gold_plan.artifacts
        ],
    }

def _is_gold_publication_complete(
    gold_run_id: int,
    file_id: int,
) -> bool:
    """
    Validate the complete Gold publication contract.

    A Gold run is complete only when:
        1. All expected physical artifacts exist.
        2. Every expected MART has a semantic model.
        3. MART grain matches the planner contract.
        4. Semantic column names and ordering match exactly.

    BASE semantic metadata is not required yet.
    """

    profiles = get_dataset_profiles(file_id)

    if not profiles:
        return False

    gold_plan = build_gold_plan(profiles)

    expected_artifact_names = {
        "base",
        *[
            artifact.artifact_name
            for artifact in gold_plan.artifacts
        ],
    }

    stored_artifacts = (
        get_gold_artifacts_for_run(
            gold_run_id
        )
    )

    stored_artifact_names = {
        artifact[3]
        for artifact in stored_artifacts
    }

    # Exact physical publication contract.
    if (
        stored_artifact_names
        != expected_artifact_names
    ):
        return False

    stored_by_name = {
        artifact[3]: artifact
        for artifact in stored_artifacts
    }

    # Validate every MART against its planner contract.
    for artifact_plan in gold_plan.artifacts:

        stored_artifact = stored_by_name.get(
            artifact_plan.artifact_name
        )

        if stored_artifact is None:
            return False

        gold_artifact_id = stored_artifact[0]

        model = get_gold_artifact_model(
            gold_artifact_id
        )

        if model is None:
            return False

        expected_catalog = (
            build_gold_artifact_catalog(
                artifact_plan
            )
        )

        # Adjust this index only if your repository model tuple
        # returns grain in a different position.
        stored_grain = model[2]
        stored_time_grain = model[3]

        if stored_grain != expected_catalog.grain:
            return False

        if stored_time_grain != expected_catalog.time_grain:
            return False

        if stored_grain != expected_catalog.grain:
            return False

        stored_columns = (
            get_gold_artifact_columns(
                gold_artifact_id
            )
        )

        if not stored_columns:
            return False

        expected_column_names = [
            column.column_name
            for column in expected_catalog.columns
        ]

        # Adjust these tuple indexes only if your repository
        # function returns a different SELECT ordering.
        stored_column_names = [
            column[2]
            for column in stored_columns
        ]

        if (
            stored_column_names
            != expected_column_names
        ):
            return False

    return True
def run_gold_processing(
    dataset_version_file_id: int,
    bucket_name: str,
) -> dict:

    # ---------------------------------------------------------
    # 1. Resolve logical dataset-file context
    # ---------------------------------------------------------
    context = get_dataset_version_file_by_id(
        dataset_version_file_id
    )

    if context is None:
        raise ValueError(
            f"Dataset-version-file "
            f"{dataset_version_file_id} not found"
        )

    dataset_version_id = context[1]
    file_id = context[2]
    status = context[3]

    # ---------------------------------------------------------
    # 2. Find Silver/DQ source for THIS DVF only
    # ---------------------------------------------------------
    dq_run = (
        get_latest_successful_dq_run_for_dataset_version_file(
            dataset_version_file_id
        )
    )

    if dq_run is None:
        raise ValueError(
            "No successful Silver/DQ run found for "
            f"dataset-version-file {dataset_version_file_id}"
        )

    dq_run_id = dq_run[0]
    source_rule_version = dq_run[3]
    silver_key = dq_run[7]

    if not silver_key:
        raise ValueError(
            f"Successful DQ run {dq_run_id} "
            "does not contain a Silver path"
        )

    # ---------------------------------------------------------
    # 3. Gold idempotency
    #
    # Reuse Gold only when it belongs to THIS DVF and was
    # created from THIS exact successful DQ run.
    # ---------------------------------------------------------
    existing_gold_run = (
        get_successful_gold_run_for_dq_run_and_dataset_version_file(
            dataset_version_file_id=dataset_version_file_id,
            source_dq_run_id=dq_run_id,
        )
    )

    # ---------------------------------------------------------
    # Check Gold publication completeness
    # ---------------------------------------------------------
    expected_artifact_names = (
        _get_expected_gold_artifact_names(
            file_id
        )
    )

    if existing_gold_run is not None:

        existing_gold_run_id = (
            existing_gold_run[0]
        )

        publication_complete = (
        _is_gold_publication_complete(
        gold_run_id=existing_gold_run_id,
        file_id=file_id,
    )
)

        if publication_complete:

            existing_artifacts = (
                get_gold_artifacts_for_run(
                    existing_gold_run_id
                )
            )

            # Self-heal lifecycle state only when the
            # Gold publication is actually complete.
            if status != "SUCCESS":
                update_dataset_version_file_status(
                    dataset_version_file_id=
                        dataset_version_file_id,
                    status="SUCCESS",
                )
            return {
                "message":
                    "Gold already processed for this "
                    "dataset-version-file and "
                    "Silver/DQ run.",
                "dataset_version_file_id":
                    dataset_version_file_id,
                "dataset_version_id":
                    dataset_version_id,
                "file_id":
                    file_id,
                "stage":
                    "GOLD",
                "status":
                    "SUCCESS",
                "already_processed":
                    True,
                "gold_run_id":
                    existing_gold_run[0],
                "attempt_id":
                    existing_gold_run[2],
                "source_dq_run_id":
                    existing_gold_run[3],
                "source_rule_version":
                    source_rule_version,
                "gold_type":
                    existing_gold_run[4],
                "row_count":
                    existing_gold_run[5],
                "gold_path":
                    existing_gold_run[6],
                "artifact_count":
                    len(existing_artifacts),
                "artifacts": [
                    {
                        "gold_artifact_id":
                            artifact[0],
                        "artifact_type":
                            artifact[2],
                        "artifact_name":
                            artifact[3],
                        "storage_path":
                            artifact[4],
                        "row_count":
                            artifact[5],
                    }
                    for artifact
                    in existing_artifacts
                ],
            }
        # A Gold run exists, but it does not satisfy
        # the current Gold publication contract.
        # Reuse this Gold run during recovery.
        recovery_gold_run = existing_gold_run

    else:
        # No previous Gold run exists.
        # This will be a normal new Gold publication.
        recovery_gold_run = None
    # ---------------------------------------------------------
    # 4. Validate DVF lifecycle
    # ---------------------------------------------------------
    if status not in (
        "READY_FOR_GOLD",
        "GOLD_FAILED",
        "SUCCESS",
    ):
        raise ValueError(
            "Dataset-version-file is not ready for Gold "
            f"processing. Current status: {status}"
        )

    # ---------------------------------------------------------
    # 5. Prevent concurrent processing for THIS DVF
    # ---------------------------------------------------------
    active_attempt = (
    get_active_processing_attempt_for_dataset_version_file(
        dataset_version_file_id
    )
)

    if active_attempt:
        raise RuntimeError(
            f"File is already being processed "
            f"in attempt {active_attempt[2]}"
        )

    # ---------------------------------------------------------
    # 6. Create Gold attempt
    # ---------------------------------------------------------
    attempt_number = (
    get_next_attempt_number_for_dataset_version_file(
        dataset_version_file_id
    )
)

    attempt = create_processing_attempt(
        file_id=file_id,
        attempt_number=attempt_number,
        stage="GOLD",
        status="PROCESSING",
        dataset_version_file_id=
            dataset_version_file_id,
    )

    attempt_id = attempt[0]

    update_dataset_version_file_status(
        dataset_version_file_id=
            dataset_version_file_id,
        status="GOLD_PROCESSING",
    )

    try:

        # -----------------------------------------------------
        # 7. Build Gold from exact Silver output
        # -----------------------------------------------------
        result = process_gold(
            file_id=file_id,
            dataset_version_id=dataset_version_id,
            rule_version=source_rule_version,
            bucket_name=bucket_name,
            silver_key=silver_key,
            attempt_id=attempt_id,
        )

        # -----------------------------------------------------
        # 8. Persist Gold lineage
        # -----------------------------------------------------
        if recovery_gold_run is not None:
            gold_run = recovery_gold_run
        else:
            gold_run = save_gold_run(
                file_id=file_id,
                attempt_id=attempt_id,
                source_dq_run_id=dq_run_id,
                gold_type="BASE",
                row_count=result["row_count"],
                gold_path=result["gold_key"],
                dataset_version_file_id=
                    dataset_version_file_id,
            )

        gold_run_id = gold_run[0]

        persisted_artifacts = []

        for artifact in result["artifacts"]:
            saved_artifact = save_gold_artifact(
                gold_run_id=gold_run_id,
                artifact_type=artifact["artifact_type"],
                artifact_name=artifact["artifact_name"],
                storage_path=artifact["storage_path"],
                row_count=artifact["row_count"],
            )

            persisted_artifacts.append(
                saved_artifact
            )
            # -----------------------------------------------------
            # Persist semantic catalog for analytical MARTs
            # -----------------------------------------------------
            catalog = artifact.get("catalog")

            if catalog is not None:
                gold_artifact_id = saved_artifact[0]

                save_gold_artifact_model(
                    gold_artifact_id=gold_artifact_id,
                    grain=catalog["grain"],
                    time_grain=catalog.get("time_grain"),
                )

                for column in catalog["columns"]:
                    save_gold_artifact_column(
                        gold_artifact_id=gold_artifact_id,
                        column_name=column["column_name"],
                        column_role=column["column_role"],
                        source_column=column["source_column"],
                        aggregation_type=
                            column["aggregation_type"],
                        ordinal_position=
                            column["ordinal_position"],
                        data_type=column["data_type"],
                    )
        stored_artifacts = (
            get_gold_artifacts_for_run(
                gold_run_id
            )
        )

        expected_artifact_names = {
            artifact["artifact_name"]
            for artifact in result["artifacts"]
        }

        stored_artifact_names = {
            artifact[3]
            for artifact in stored_artifacts
        }

        if (
            stored_artifact_names
            != expected_artifact_names
        ):
            raise RuntimeError(
                "Gold artifact persistence incomplete. "
                f"Expected artifacts="
                f"{sorted(expected_artifact_names)}, "
                f"stored artifacts="
                f"{sorted(stored_artifact_names)}, "
                f"gold_run_id={gold_run_id}"
            )

        # -----------------------------------------------------
        # 9. Complete processing attempt
        # -----------------------------------------------------
        complete_processing_attempt(
            attempt_id=attempt_id,
            status="SUCCESS",
        )

        update_dataset_version_file_status(
            dataset_version_file_id=
                dataset_version_file_id,
            status="SUCCESS",
        )
        recovered_existing_run = (
                    recovery_gold_run is not None
                )
        return {
            "dataset_version_file_id":
                dataset_version_file_id,
            "dataset_version_id":
                dataset_version_id,
            "file_id": file_id,
            "stage": "GOLD",
            "status": "SUCCESS",
            "already_processed": False,
            "recovered_existing_run": recovered_existing_run,
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "gold_run_id": gold_run[0],
            "original_gold_attempt_id": (
                recovery_gold_run[2]
                if recovery_gold_run is not None
                else None
            ),
            "source_dq_run_id": dq_run_id,
            "source_rule_version":
                source_rule_version,
            "gold_type": "BASE",
            "row_count": result["row_count"],
            "gold_path": result["gold_key"],
            "artifact_count": len(stored_artifacts),
            "artifacts": [
                {
                    "gold_artifact_id":
                        artifact[0],
                    "artifact_type":
                        artifact[2],
                    "artifact_name":
                        artifact[3],
                    "storage_path":
                        artifact[4],
                    "row_count":
                        artifact[5],
                }
                for artifact in stored_artifacts
            ],

            "gold_plan":
                result["gold_plan"],
        }

    except Exception as exc:

        complete_processing_attempt(
            attempt_id=attempt_id,
            status="FAILED",
            error_message=str(exc),
        )

        # IMPORTANT:
        # Failure belongs to this logical DVF, not the
        # shared physical file.
        update_dataset_version_file_status(
            dataset_version_file_id=
                dataset_version_file_id,
            status="GOLD_FAILED",
        )

        raise RuntimeError(
            f"Gold processing failed: {exc}"
        ) from exc
