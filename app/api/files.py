import os
import tempfile
from unittest import result
from app.services.processing_service import start_processing
from app.services.file_service import (
    register_file,
    finalize_presigned_upload,
)
from app.services.file_service import (
    register_file,
    finalize_presigned_upload,
)
from app.storage.s3_service import (
    generate_presigned_upload_url,
    get_object_metadata,
)
from pydantic import BaseModel
from uuid import uuid4


from app.storage.s3_service import generate_presigned_upload_url

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.utils.hashing import calculate_file_hash
from app.services.file_service import register_file

class FileCompleteRequest(BaseModel):
    user_id: int
    object_key: str
    expected_file_size: int

class FileInitiateRequest(BaseModel):
    user_id: int
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
                "status": upload[3],
                "created_at": upload[4],
            }
        }

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

@router.post("/initiate")
def initiate_upload(request: FileInitiateRequest):

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
            "status": upload[3],
            "created_at": upload[4],
        }
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
    except RuntimeError as exc:
        raise HTTPException(
        status_code=409,
        detail=str(exc),
    )

    attempt = result["attempt"]
    bronze = result["bronze"]
    attempt = result["attempt"]

    return {
        "message": "Bronze processing completed successfully",
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