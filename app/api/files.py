import os
import tempfile
from uuid import uuid4

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel

from app.services.processing_service import (
    start_processing,
    run_silver_processing,
    run_gold_processing,
)
from app.services.file_service import (
    register_file,
    finalize_presigned_upload,
    validate_upload_context,
)
from app.services.business_rule_service import (
    get_business_rule_questions,
    submit_business_rule_answers,
    finalize_business_rules,
    update_business_rule_answer,
)
from app.storage.s3_service import (
    generate_presigned_upload_url,
    get_object_metadata,
)
from app.utils.hashing import calculate_file_hash
from app.schemas.business_rules import (
    BusinessRuleSubmission,
    BusinessRuleUpdate,
)
class FileCompleteRequest(BaseModel):
    user_id: int
    workspace_id: int | None = None
    dataset_id: int | None = None
    object_key: str
    expected_file_size: int

class FileInitiateRequest(BaseModel):
    user_id: int
    workspace_id: int | None = None
    dataset_id: int | None = None
    file_name: str
    file_size: int
    content_type: str | None = None

router = APIRouter(
    prefix="/files",
    tags=["Files"]
)


@router.post("/upload")
async def upload_file(
    user_id: int = Form(...),
    workspace_id: int | None = Form(None),
    dataset_id: int | None = Form(None),
    file: UploadFile = File(...),
):
    allowed_extensions = {".csv"}

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="File name is missing"
        )

    _, extension = os.path.splitext(file.filename)

    if extension.lower() not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Only CSV files are currently supported"
        )

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=extension
        ) as temp_file:

            while chunk := await file.read(1024 * 1024):
                temp_file.write(chunk)

            temp_path = temp_file.name

        file_size = os.path.getsize(temp_path)

        file_hash = calculate_file_hash(temp_path)

        result = register_file(
            user_id=user_id,
            file_name=file.filename,
            file_size=file_size,
            file_hash=file_hash,
            local_file_path=temp_path,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
        )

        physical_file = result["file"]
        upload = result["upload_request"]

        return {
            "message": (
                "Existing physical file reused"
                if result["is_duplicate"]
                else "New physical file uploaded successfully"
            ),
            "is_duplicate": result["is_duplicate"],
            "file": {
                "file_id": physical_file[0],
                "file_name": physical_file[1],
                "file_size": physical_file[2],
                "file_hash": physical_file[3],
                "storage_path": physical_file[4],
                "status": physical_file[5],
            },
            "upload_request": {
                "upload_id": upload[0],
                "user_id": upload[1],
                "file_id": upload[2],
                "workspace_id": upload[3],
                "dataset_id": upload[4],
                "status": upload[5],
                "created_at": upload[6],
            },
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

@router.post("/initiate")
def initiate_upload(request: FileInitiateRequest):

    try:
        validate_upload_context(
            user_id=request.user_id,
            workspace_id=request.workspace_id,
            dataset_id=request.dataset_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    upload_token = str(uuid4())

    object_key = (
        f"staging/"
        f"user-{request.user_id}/"
        f"{upload_token}/"
        f"{request.file_name}"
    )

    presigned_url = generate_presigned_upload_url(
        object_key=object_key,
        expires_in=900,
    )

    return {
        "message": "Upload session created",
        "user_id": request.user_id,
        "file_name": request.file_name,
        "file_size": request.file_size,
        "content_type": request.content_type,
        "object_key": object_key,
        "presigned_url": presigned_url,
        "expires_in": 900,
    }

@router.post("/complete")
def complete_upload(request: FileCompleteRequest):

    try:
        result = finalize_presigned_upload(
            user_id=request.user_id,
            object_key=request.object_key,
            expected_file_size=request.expected_file_size,
            workspace_id=request.workspace_id,
            dataset_id=request.dataset_id,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:
        print(f"Upload finalization failed: {exc}")

        raise HTTPException(
            status_code=500,
            detail="Upload finalization failed",
        )

    physical_file = result["file"]
    upload = result["upload_request"]

    return {
        "message": (
            "Existing physical file reused"
            if result["is_duplicate"]
            else "Upload finalized successfully"
        ),

        "is_duplicate": result["is_duplicate"],

        "file": {
            "file_id": physical_file[0],
            "file_name": physical_file[1],
            "file_size": physical_file[2],
            "file_hash": physical_file[3],
            "storage_path": physical_file[4],
            "status": physical_file[5],
        },

        "upload_request": {
            "upload_id": upload[0],
            "user_id": upload[1],
            "file_id": upload[2],
            "workspace_id": upload[3],
            "dataset_id": upload[4],
            "status": upload[5],
            "created_at": upload[6],
        },
    }

@router.post("/{file_id}/process")
def process_file(file_id: int):

    try:
        result = start_processing(file_id)

    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    attempt = result["attempt"]
    bronze = result["bronze"]

    return {
        "message": (
            "Bronze processing and dataset profiling completed. "
            "Business rule configuration is required before Silver processing."
            ),
        "pipeline_status": "AWAITING_RULES",
        "processing_attempt": {
            "attempt_id": attempt[0],
            "file_id": attempt[1],
            "attempt_number": attempt[2],
            "stage": attempt[3],
            "status": attempt[4],
            "error_message": attempt[5],
            "started_at": attempt[6],
            "completed_at": attempt[7],
        },
        "bronze": {
            "object_key": bronze["bronze_object_key"],
            "row_count": bronze["row_count"],
            "columns": bronze["column_names"],
            "schema": bronze["schema"],
        }
    }

@router.get("/{file_id}/business-rules/suggestions")
def get_rule_suggestions(file_id: int):
    try:
        return get_business_rule_questions(file_id)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

@router.post("/{file_id}/business-rules")
def submit_business_rules(
    file_id: int,
    submission: BusinessRuleSubmission,
):
    try:
        return submit_business_rule_answers(
            file_id=file_id,
            answers=submission.answers,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
@router.post("/{file_id}/business-rules/finalize")
def finalize_rules(file_id: int):
    try:
        return finalize_business_rules(file_id)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
@router.post("/{file_id}/silver/process")
def process_file_silver(file_id: int):
    try:
        result = run_silver_processing(
            file_id=file_id,
            bucket_name="ai-data-workspace-amir-dev",
        )

        return {
            "message": "Silver processing completed successfully.",
            **result,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except RuntimeError as exc:
        message = str(exc)

        if "already being processed" in message:
            raise HTTPException(
                status_code=409,
                detail=message,
            )

        raise HTTPException(
            status_code=500,
            detail=message,
        )

@router.patch(
    "/{file_id}/business-rules"
)
def update_business_rule(
    file_id: int,
    request: BusinessRuleUpdate,
):

    try:
        result = update_business_rule_answer(
            file_id=file_id,
            item=request,
        )

        return result

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

@router.post("/{file_id}/gold/process")
def process_file_gold(
    file_id: int,
):
    try:
        result = run_gold_processing(
            file_id=file_id,
            bucket_name="ai-data-workspace-amir-dev",
        )

        return {
            "message": (
                "Gold processing completed successfully."
                if not result["already_processed"]
                else "Gold already processed. Existing result returned."
            ),
            **result,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )