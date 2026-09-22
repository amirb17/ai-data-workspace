from app.db.file_repository import (
    get_physical_file_by_id,
    get_next_attempt_number,
    create_processing_attempt,
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
    physical_file = get_physical_file_by_id(file_id)

    if not physical_file:
        raise ValueError("Physical file not found")

    file_status = physical_file[5]

    # Silver should only start after rules are finalized.
    if file_status not in ("PROCESSING", "FAILED"):
        raise ValueError(
            f"Silver processing cannot start. "
            f"Current file status: {file_status}"
        )

    # Prevent concurrent processing.
    active_attempt = get_active_processing_attempt(file_id)

    if active_attempt:
        raise RuntimeError(
            f"File is already being processed "
            f"in attempt {active_attempt[2]}"
        )

    # Get next attempt number.
    attempt_number = get_next_attempt_number(file_id)

    # 1. Create SILVER attempt.
    attempt = create_processing_attempt(
        file_id=file_id,
        attempt_number=attempt_number,
        stage="SILVER",
        status="PROCESSING",
    )

    attempt_id = attempt[0]

    try:
        # 2. Run Silver.
        result = process_silver(
            file_id=file_id,
            bucket_name=bucket_name,
        )

        # 3. Save DQ metadata.
        dq_run = save_data_quality_run(
            file_id=file_id,
            attempt_id=attempt_id,
            total_rows=result["total_rows"],
            valid_rows=result["valid_rows"],
            rejected_rows=result["rejected_rows"],
            silver_path=result["silver_key"],
            quarantine_path=result["quarantine_key"],
        )
        dq_run_id = dq_run[0]

        for issue in result["dq_issues"]:
            save_data_quality_issue(
                dq_run_id=dq_run_id,
                column_name=issue["column_name"],
                rule_type=issue["rule_type"],
                violation_count=issue["violation_count"],
            )
        # 4. Mark SILVER attempt successful.
        complete_processing_attempt(
            attempt_id=attempt_id,
            status="SUCCESS",
        )

        return {
            "file_id": file_id,
            "stage": "SILVER",
            "status": "SUCCESS",
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "dq_run_id": dq_run[0],
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
        )