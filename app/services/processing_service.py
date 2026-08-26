from app.db.file_repository import (
    get_physical_file_by_id,
    get_next_attempt_number,
    create_processing_attempt,
)
from app.db.file_repository import (
    get_physical_file_by_id,
    get_next_attempt_number,
    create_processing_attempt,
    get_active_processing_attempt,
    update_physical_file_status,
    complete_processing_attempt,
)
from app.processing.bronze_processor import run_bronze_stage


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

        completed_attempt = complete_processing_attempt(
            attempt_id=attempt_id,
            status="SUCCESS",
        )

        update_physical_file_status(
        file_id=file_id,
        status="PROCESSING",
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