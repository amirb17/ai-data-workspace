import os
import tempfile

from fastapi import Depends, APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel, Field
from app.api.identity import get_current_user
from app.services.upload_session_service import initiate_upload_session, complete_upload_session

from app.services.processing_service import (
    start_processing,
)
from app.services.file_service import (
    register_file,
)
from app.services.business_rule_service import (
    get_business_rule_questions,
    submit_business_rule_answers,
    finalize_business_rules,
)
from app.utils.hashing import calculate_file_hash
from app.schemas.business_rules import (
    BusinessRuleSubmission,
    BusinessRuleUpdate,
)
from app.services.processing_context_service import (
    owned_upload, public_rules, read_processing_context,
    rule_mutation, StaleRuleVersion, owned_rule_context,
)
from app.services.delivery_execution_service import continue_processing
from app.db.database import get_connection
from app.db.dataset_repository import get_upload_processing_association


from app.api.processing_errors import public_call as _public_call

class RuleFinalizeRequest(BaseModel):
    expected_rule_version: int | None = Field(default=None, ge=0)
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
def process_file(upload_id: int, user: dict = Depends(get_current_user), workspace_id: int | None = None, dataset_id: int | None = None):
    def operation():
        owned_upload(upload_id, user, workspace_id, dataset_id)
        start_processing(upload_id)
        return read_processing_context(upload_id, user, workspace_id, dataset_id)
    return _public_call(operation)


@router.get("/uploads/{upload_id}/processing-context")
def processing_context(upload_id: int, user: dict = Depends(get_current_user), workspace_id: int | None = None, dataset_id: int | None = None):
    return _public_call(lambda: read_processing_context(upload_id, user, workspace_id, dataset_id))


@router.post("/uploads/{upload_id}/continue")
def continue_file(upload_id: int, user: dict = Depends(get_current_user), workspace_id: int | None = None, dataset_id: int | None = None):
    return _public_call(lambda: continue_processing(upload_id, user, workspace_id, dataset_id))


def _continue_owned_association(association_id, user, workspace_id, dataset_id, stage):
    context = owned_rule_context(association_id, user, workspace_id, dataset_id)
    with get_connection() as conn:
        upload = conn.execute("""SELECT upload_id FROM upload_requests WHERE user_id=%s AND workspace_id=%s
            AND dataset_id=%s AND file_id=%s AND status='UPLOADED' ORDER BY upload_id DESC LIMIT 1""",
            (user["user_id"], context[6], context[5], context[2])).fetchone()
    if not upload:
        raise PermissionError("Completed owned delivery required")
    resolved = get_upload_processing_association(context[5], context[2])
    if resolved is None or resolved[0] != association_id:
        raise ValueError("Association does not match the authoritative upload context")
    return continue_processing(upload[0], user, context[6], context[5], stage)

@router.get(
    "/dataset-version-files/{dataset_version_file_id}/business-rules/suggestions"
)
def get_rule_suggestions(
    dataset_version_file_id: int,
    user: dict = Depends(get_current_user),
    workspace_id: int | None = None, dataset_id: int | None = None,
):
    return _public_call(lambda: public_rules(dataset_version_file_id, user, workspace_id, dataset_id))

@router.post(
    "/dataset-version-files/{dataset_version_file_id}/business-rules"
)
def submit_business_rules(
    dataset_version_file_id: int,
    submission: BusinessRuleSubmission,
    user: dict = Depends(get_current_user),
    workspace_id: int | None = None, dataset_id: int | None = None,
):
    def operation():
        with rule_mutation(dataset_version_file_id, user, submission.expected_rule_version, workspace_id, dataset_id):
            result = submit_business_rule_answers(dataset_version_file_id, submission.answers)
            return {key: result[key] for key in ("rule_version", "rules_changed", "saved_rule_count", "skipped_answer_count")}
    return _public_call(operation)
@router.post(
    "/dataset-version-files/"
    "{dataset_version_file_id}/business-rules/finalize"
)
def finalize_rules(
    dataset_version_file_id: int,
    request: RuleFinalizeRequest | None = None,
    user: dict = Depends(get_current_user),
    workspace_id: int | None = None, dataset_id: int | None = None,
):
    def operation():
        with rule_mutation(dataset_version_file_id, user, request.expected_rule_version if request else None, workspace_id, dataset_id):
            return finalize_business_rules(dataset_version_file_id)
    return _public_call(operation)
@router.post(
    "/dataset-version-files/{dataset_version_file_id}/silver/process"
)
def process_file_silver(
    dataset_version_file_id: int,
    user: dict = Depends(get_current_user),
    workspace_id: int | None = None, dataset_id: int | None = None,
):
    return _public_call(lambda: _continue_owned_association(dataset_version_file_id, user, workspace_id, dataset_id, "SILVER"))
@router.patch(
    "/dataset-version-files/"
    "{dataset_version_file_id}/business-rules"
)
def update_business_rule(
    dataset_version_file_id: int,
    request: BusinessRuleUpdate,
    user: dict = Depends(get_current_user),
    workspace_id: int | None = None, dataset_id: int | None = None,
):
    def operation():
        # Historical replay and versioned rule editing require a separate explicit flow.
        owned_rule_context(dataset_version_file_id, user, workspace_id, dataset_id)
        raise StaleRuleVersion("Historical rule editing is unavailable")
    return _public_call(operation)
@router.post(
    "/dataset-version-files/{dataset_version_file_id}/gold/process"
)
def process_file_gold(
    dataset_version_file_id: int,
    user: dict = Depends(get_current_user),
    workspace_id: int | None = None, dataset_id: int | None = None,
):
    return _public_call(lambda: _continue_owned_association(dataset_version_file_id, user, workspace_id, dataset_id, "GOLD"))
