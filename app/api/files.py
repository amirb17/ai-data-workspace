import os
import tempfile

from fastapi import Depends, APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel, Field
from app.api.identity import get_current_user
from app.services.upload_session_service import initiate_upload_session, complete_upload_session

from app.services.processing_service import (
    start_processing,
    run_silver_processing,
    run_gold_processing,
)
from app.services.file_service import (
    register_file,
)
from app.services.business_rule_service import (
    get_business_rule_questions,
    submit_business_rule_answers,
    finalize_business_rules,
    update_business_rule_answer,
)
from app.utils.hashing import calculate_file_hash
from app.schemas.business_rules import (
    BusinessRuleSubmission,
    BusinessRuleUpdate,
)
from app.config import S3_BUCKET_NAME
class FileCompleteRequest(BaseModel):
    upload_request_id: int = Field(gt=0)

class FileInitiateRequest(BaseModel):
    user_id: int | None = Field(default=None, gt=0)
    workspace_id: int = Field(gt=0)
    dataset_id: int = Field(gt=0)
    file_name: str = Field(min_length=1, max_length=255)
    file_size: int = Field(gt=0)
    content_type: str | None = Field(default=None, max_length=255)

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
    user: dict = Depends(get_current_user),
):
    if user_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="User identity does not match current principal")
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
def initiate_upload(request: FileInitiateRequest, user: dict = Depends(get_current_user)):
    if request.user_id is not None and request.user_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="User identity does not match current principal")
    try:
        return initiate_upload_session(user_id=user["user_id"], workspace_id=request.workspace_id,
            dataset_id=request.dataset_id, file_name=request.file_name, file_size=request.file_size, content_type=request.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Upload initiation unavailable") from exc

@router.post("/complete")
def complete_upload(request: FileCompleteRequest, user: dict = Depends(get_current_user)):
    try:
        return complete_upload_session(request.upload_request_id, user["user_id"])
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Upload session access denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Upload completion failed; retry the same upload_request_id") from exc

@router.post("/uploads/{upload_id}/process")
def process_file(upload_id: int):
    try:
        result = start_processing(upload_id)

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
    dataset_version = result["dataset_version"]
    dataset_version_file = result["dataset_version_file"]
    processing_attempt = None

    if attempt is not None:
        processing_attempt = {
            "attempt_id": attempt[0],
            "file_id": attempt[1],
            "attempt_number": attempt[2],
            "stage": attempt[3],
            "status": attempt[4],
            "error_message": attempt[5],
            "started_at": attempt[6],
            "completed_at": attempt[7],
        }

    return {
        "message": (
            "Bronze processing and dataset profiling completed. "
            "Business rule configuration is required before Silver processing."
            ),
        "pipeline_status": "AWAITING_RULES",
        "processing_attempt": processing_attempt,
        "bronze": {
            "object_key": bronze.get("bronze_object_key"),
            "row_count": bronze.get("row_count"),
            "columns": bronze.get("column_names"),
            "schema": bronze.get("schema"),
            "schema_hash": bronze["schema_hash"],
            "reused": bronze.get("reused", False),
        },
        "context": {
            "upload_id": result["upload_id"],
            "workspace_id": result["workspace_id"],
            "dataset_id": result["dataset_id"],
            "dataset_version_id": dataset_version["dataset_version_id"],
            "dataset_version_number": dataset_version["version_number"],
            "dataset_version_created": dataset_version["created"],
            "dataset_version_file_id": dataset_version_file[0],
        },
    }

@router.get(
    "/dataset-version-files/{dataset_version_file_id}/business-rules/suggestions"
)
def get_rule_suggestions(
    dataset_version_file_id: int,
):
    try:
        return get_business_rule_questions(
            dataset_version_file_id
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

@router.post(
    "/dataset-version-files/{dataset_version_file_id}/business-rules"
)
def submit_business_rules(
    dataset_version_file_id: int,
    submission: BusinessRuleSubmission,
):
    try:
        return submit_business_rule_answers(
            dataset_version_file_id=dataset_version_file_id,
            answers=submission.answers,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
@router.post(
    "/dataset-version-files/"
    "{dataset_version_file_id}/business-rules/finalize"
)
def finalize_rules(
    dataset_version_file_id: int,
):
    try:
        return finalize_business_rules(
            dataset_version_file_id
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
@router.post(
    "/dataset-version-files/{dataset_version_file_id}/silver/process"
)
def process_file_silver(
    dataset_version_file_id: int,
):
    try:
        result = run_silver_processing(
            dataset_version_file_id=dataset_version_file_id,
            bucket_name=S3_BUCKET_NAME,
        )

        return {
            "message": (
                "Silver processing completed successfully."
                if not result["already_processed"]
                else (
                    "Silver already processed for the current "
                    "dataset rule version."
                )
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
            status_code=409,
            detail=str(exc),
        )
@router.patch(
    "/dataset-version-files/"
    "{dataset_version_file_id}/business-rules"
)
def update_business_rule(
    dataset_version_file_id: int,
    request: BusinessRuleUpdate,
):

    try:
        result = update_business_rule_answer(
            dataset_version_file_id=dataset_version_file_id,
            item=request,
        )

        return result

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
@router.post(
    "/dataset-version-files/{dataset_version_file_id}/gold/process"
)
def process_file_gold(
    dataset_version_file_id: int,
):
    try:
        result = run_gold_processing(
            dataset_version_file_id=dataset_version_file_id,
            bucket_name=S3_BUCKET_NAME,
        )

        return {
            "message": (
                "Gold processing completed successfully."
                if not result["already_processed"]
                else (
                    "Gold already processed for this "
                    "dataset-version-file and Silver/DQ run."
                )
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
            status_code=409,
            detail=str(exc),
        )