import os
import tempfile

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from app.utils.hashing import calculate_file_hash
from app.services.file_service import register_file


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