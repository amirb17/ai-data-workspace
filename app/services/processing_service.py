from app.processing.gold_processor import process_gold
from app.db.file_repository import (
    get_latest_successful_dq_run,
    get_latest_successful_gold_run,
    get_physical_file_by_id,
    get_next_attempt_number,
    create_processing_attempt,
    get_rule_version,
    get_successful_gold_run_for_dq_run,
)
from app.processing.silver_processor import process_silver
from app.db.file_repository import (
    get_physical_file_by_id,
    get_next_attempt_number,
    create_processing_attempt,
    get_active_processing_attempt,
    update_physical_file_status,
    complete_processing_attempt,
    save_data_quality_run,
    save_gold_run,
)
from app.db.file_repository import save_dataset_profiles
from app.processing.bronze_processor import run_bronze_stage
from app.db.file_repository import save_dataset_profile_summary
from app.db.file_repository import save_data_quality_issue

def start_processing(file_id: int):
    physical_file = get_physical_file_by_id(file_id)

    if not physical_file:
        raise ValueError("Physical file not found")

    storage_path = physical_file[4]
    file_status = physical_file[5]

    if not storage_path:
        raise ValueError(
            "Physical file does not have a storage path"
        )

    active_attempt = get_active_processing_attempt(file_id)

    if active_attempt:
        raise RuntimeError(
            f"File is already being processed "
            f"in attempt {active_attempt[2]}"
        )

    if file_status not in ("UPLOADED", "FAILED"):
        raise ValueError(
            f"File cannot be processed. "
            f"Current status: {file_status}"
        )

    attempt_number = get_next_attempt_number(file_id)

    attempt = create_processing_attempt(
        file_id=file_id,
        attempt_number=attempt_number,
        stage="BRONZE",
        status="PROCESSING",
    )
    update_physical_file_status(
        file_id=file_id,
        status="PROCESSING",
    )

    attempt_id = attempt[0]

    raw_object_key = storage_path.replace(
        "s3://ai-data-workspace-amir-dev/",
        "",
    )

    try:
        bronze_result = run_bronze_stage(
            file_id=file_id,
            raw_object_key=raw_object_key,
        )
        save_dataset_profiles(
        file_id=file_id,
        profiles=bronze_result["profiles"],
        )
        save_dataset_profile_summary(
        file_id=file_id,
        total_rows=bronze_result["row_count"],
        total_columns=len(bronze_result["column_names"]),
        )

        completed_attempt = complete_processing_attempt(
            attempt_id=attempt_id,
            status="SUCCESS",
        )

        update_physical_file_status(
        file_id=file_id,
        status="AWAITING_RULES",
        )

        return {
            "file": physical_file,
            "attempt": completed_attempt,
            "bronze": bronze_result,
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

    update_physical_file_status(
        file_id=file_id,
        status="PROCESSING",
    )

    return {
        "file": physical_file,
        "attempt": attempt,
    }
def run_silver_processing(
    file_id: int,
    bucket_name: str,
):
    # ---------------------------------------------------------
    # 1. Verify physical file exists
    # ---------------------------------------------------------
    physical_file = get_physical_file_by_id(file_id)

    if not physical_file:
        raise ValueError("Physical file not found")

    file_status = physical_file[5]

    # ---------------------------------------------------------
    # 2. Get current rule version
    # ---------------------------------------------------------
    current_rule_version = get_rule_version(file_id)

    # ---------------------------------------------------------
    # 3. SILVER IDEMPOTENCY CHECK
    #
    # If Silver has already succeeded using the current
    # rule version, do NOT process it again.
    # ---------------------------------------------------------
    latest_dq_run = get_latest_successful_dq_run(file_id)

    if latest_dq_run:
        silver_rule_version = latest_dq_run[3]

        if silver_rule_version == current_rule_version:
            return {
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
    # 4. Silver needs processing/reprocessing
    # ---------------------------------------------------------
    #
    # PROCESSING:
    #   normal flow after rules finalized
    #
    # FAILED:
    #   retry failed Silver/Gold processing
    #
    # SUCCESS:
    #   important!
    #   allows Silver reprocessing when rules changed after
    #   the previous complete pipeline.
    # ---------------------------------------------------------
    if file_status not in (
        "PROCESSING",
        "FAILED",
        "SUCCESS",
    ):
        raise ValueError(
            f"Silver processing cannot start. "
            f"Current file status: {file_status}"
        )

    # ---------------------------------------------------------
    # 5. Prevent concurrent processing
    # ---------------------------------------------------------
    active_attempt = get_active_processing_attempt(file_id)

    if active_attempt:
        raise RuntimeError(
            f"File is already being processed "
            f"in attempt {active_attempt[2]}"
        )

    # ---------------------------------------------------------
    # 6. Get next attempt number
    # ---------------------------------------------------------
    attempt_number = get_next_attempt_number(file_id)

    # ---------------------------------------------------------
    # 7. Create SILVER processing attempt
    # ---------------------------------------------------------
    attempt = create_processing_attempt(
        file_id=file_id,
        attempt_number=attempt_number,
        stage="SILVER",
        status="PROCESSING",
    )

    attempt_id = attempt[0]

    try:
        # -----------------------------------------------------
        # 8. Run Silver processing
        # -----------------------------------------------------
        result = process_silver(
            file_id=file_id,
            bucket_name=bucket_name,
        )

        # -----------------------------------------------------
        # 9. Save DQ run WITH rule version
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
        )

        dq_run_id = dq_run[0]

        # -----------------------------------------------------
        # 10. Save DQ issue metrics
        # -----------------------------------------------------
        for issue in result["dq_issues"]:
            save_data_quality_issue(
                dq_run_id=dq_run_id,
                column_name=issue["column_name"],
                rule_type=issue["rule_type"],
                violation_count=issue["violation_count"],
            )

        # -----------------------------------------------------
        # 11. Mark attempt successful
        # -----------------------------------------------------
        complete_processing_attempt(
            attempt_id=attempt_id,
            status="SUCCESS",
        )

        return {
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
            "quarantine_path": result["quarantine_key"],
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
            f"Silver processing failed: {exc}"
        ) from exc
def run_gold_processing(
    file_id: int,
    bucket_name: str,
) -> dict:

    # ---------------------------------------------------------
    # 1. Verify physical file exists
    # ---------------------------------------------------------
    physical_file = get_physical_file_by_id(
        file_id
    )

    if not physical_file:
        raise ValueError(
            f"Physical file {file_id} not found"
        )

    file_status = physical_file[5]

    # ---------------------------------------------------------
    # 2. Find latest successful Silver/DQ source
    # ---------------------------------------------------------
    dq_run = get_latest_successful_dq_run(
        file_id
    )

    if not dq_run:
        raise ValueError(
            f"No successful Silver/DQ run found "
            f"for file {file_id}"
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
    # 3. GOLD IDEMPOTENCY CHECK
    #
    # Gold is reusable only if it was built from this exact
    # successful Silver/DQ run.
    # ---------------------------------------------------------
    existing_gold_run = (
        get_successful_gold_run_for_dq_run(
            file_id=file_id,
            source_dq_run_id=dq_run_id,
        )
    )

    if existing_gold_run:
        return {
            "file_id": file_id,
            "stage": "GOLD",
            "status": "SUCCESS",
            "already_processed": True,
            "gold_run_id": existing_gold_run[0],
            "attempt_id": existing_gold_run[2],
            "source_dq_run_id": existing_gold_run[3],
            "source_rule_version": source_rule_version,
            "gold_type": existing_gold_run[4],
            "row_count": existing_gold_run[5],
            "gold_path": existing_gold_run[6],
        }

    # ---------------------------------------------------------
    # 4. Gold actually needs processing
    # ---------------------------------------------------------
    if file_status not in (
        "PROCESSING",
        "FAILED",
        "SUCCESS",
    ):
        raise ValueError(
            f"File {file_id} is not ready for Gold processing. "
            f"Current status: {file_status}"
        )

    # ---------------------------------------------------------
    # 5. Prevent concurrent processing
    # ---------------------------------------------------------
    active_attempt = get_active_processing_attempt(
        file_id
    )

    if active_attempt:
        raise RuntimeError(
            f"File is already being processed "
            f"in attempt {active_attempt[2]}"
        )

    # ---------------------------------------------------------
    # 6. Create new Gold attempt
    # ---------------------------------------------------------
    attempt_number = get_next_attempt_number(
        file_id
    )

    attempt = create_processing_attempt(
        file_id=file_id,
        attempt_number=attempt_number,
        stage="GOLD",
        status="PROCESSING",
    )

    attempt_id = attempt[0]

    try:
        # -----------------------------------------------------
        # 7. Build Gold from the exact Silver source
        # -----------------------------------------------------
        result = process_gold(
            file_id=file_id,
            bucket_name=bucket_name,
            silver_key=silver_key,
            attempt_id=attempt_id,
        )

        # -----------------------------------------------------
        # 8. Register Gold lineage
        # -----------------------------------------------------
        gold_run = save_gold_run(
            file_id=file_id,
            attempt_id=attempt_id,
            source_dq_run_id=dq_run_id,
            gold_type="BASE",
            row_count=result["row_count"],
            gold_path=result["gold_key"],
        )

        # -----------------------------------------------------
        # 9. Complete attempt
        # -----------------------------------------------------
        complete_processing_attempt(
            attempt_id=attempt_id,
            status="SUCCESS",
        )

        update_physical_file_status(
            file_id=file_id,
            status="SUCCESS",
        )

        return {
            "file_id": file_id,
            "stage": "GOLD",
            "status": "SUCCESS",
            "already_processed": False,
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "gold_run_id": gold_run[0],
            "source_dq_run_id": dq_run_id,
            "source_rule_version": source_rule_version,
            "gold_type": "BASE",
            "row_count": result["row_count"],
            "gold_path": result["gold_key"],
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
            f"Gold processing failed: {exc}"
        ) from exc