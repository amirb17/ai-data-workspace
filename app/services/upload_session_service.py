import os
from contextlib import nullcontext
from uuid import uuid4
from app.db.database import repository_transaction
from app.db.upload_session_repository import create_upload_session, locked_upload_session, complete_session, fail_session
from app.db.file_repository import find_physical_file_by_hash, create_uploaded_physical_file
from app.services.file_service import validate_upload_context
from app.storage.s3_service import (generate_presigned_upload_url, get_object_metadata,
    calculate_s3_object_hash, promote_staging_object_to_raw, build_s3_uri, delete_s3_object)


def initiate_upload_session(user_id: int, workspace_id: int, dataset_id: int, file_name: str, file_size: int, content_type: str | None = None) -> dict:
    if workspace_id is None or dataset_id is None:
        raise ValueError("Workspace and dataset are required for an upload session")
    validate_upload_context(user_id, workspace_id, dataset_id)
    if not file_name.lower().endswith('.csv') or any(char in file_name for char in ('/', '\\', '\x00')) or '..' in file_name:
        raise ValueError("A plain CSV filename is required")
    if file_size <= 0:
        raise ValueError("File size must be positive")
    object_key = f"staging/user-{user_id}/{uuid4()}/{file_name}"
    # Signing failure must not leave a session that the caller never received.
    url = generate_presigned_upload_url(object_key=object_key, expires_in=900)
    session = create_upload_session(user_id, workspace_id, dataset_id, object_key, file_name, file_size, content_type)
    return {"upload_request_id": session['upload_id'], "session_status": "INITIATED", "user_id": user_id,
            "workspace_id": workspace_id, "dataset_id": dataset_id, "file_name": file_name,
            "file_size": file_size, "content_type": content_type, "object_key": object_key,
            "presigned_url": url, "expires_in": 900}


def complete_upload_session(upload_id: int, user_id: int) -> dict:
    durable = os.getenv('DATARISE_PROCESSING_DEPLOYMENT_APPROVED') == 'true'
    raw_identity = None
    result = None
    failure = None
    object_key = None
    with (repository_transaction() if durable else nullcontext()), locked_upload_session(upload_id) as (conn, session):
        if not session or session['staging_object_key'] is None:
            raise ValueError("Upload session not found")
        # Current resource ownership authorizes completion; uploader is immutable provenance.
        # Unattached historical sessions have no adoptable resource boundary.
        if session['workspace_id'] is None and session['dataset_id'] is None and session['user_id'] != user_id:
            raise PermissionError('Upload session access denied')
        validate_upload_context(user_id, session['workspace_id'], session['dataset_id'])
        if session['completed_at'] is not None:
            return session['completion_result']
        object_key = session['staging_object_key']
        try:
            metadata = get_object_metadata(object_key)
            if metadata['size'] != session['expected_file_size']:
                raise ValueError("File size does not match the initiated upload")
            expected_type = session['content_type']
            if expected_type and metadata.get('content_type') != expected_type:
                raise ValueError("File content type does not match the initiated upload")
            if durable:
                from app.processing_worker.runtime_security import require_runtime
                from app.storage.s3_service import get_s3_client, BUCKET_NAME
                from app.storage.versioned_ingestion import read_staged
                require_runtime(conn, 'api')
                upload_client = get_s3_client()
                raw_payload, file_hash = read_staged(upload_client, BUCKET_NAME, object_key,
                    session['expected_file_size'], session['content_type'])
            else:
                file_hash = calculate_s3_object_hash(object_key)
            physical = find_physical_file_by_hash(file_hash)
            duplicate = physical is not None
            if physical is None:
                if durable:
                    from app.storage.versioned_ingestion import write_raw
                    raw_identity = write_raw(upload_client, BUCKET_NAME, raw_payload, file_hash, session['source_file_name'])
                    raw_key = raw_identity.key
                else:
                    raw_key = promote_staging_object_to_raw(staging_object_key=object_key, file_hash=file_hash, file_name=session['source_file_name'])
                physical, inserted = create_uploaded_physical_file(file_name=session['source_file_name'], file_size=metadata['size'], file_hash=file_hash, storage_path=build_s3_uri(raw_key))
                duplicate = not inserted
                if durable and not inserted:
                    # A concurrent committed completion owns the shared source pin.
                    # Our unused unique Raw object remains an unreferenced orphan.
                    raw_identity = None
            created_at = session['created_at'].isoformat()
            result = {"message": "Existing physical file reused" if duplicate else "Upload finalized successfully",
                "is_duplicate": duplicate, "upload_request_id": upload_id, "session_status": "COMPLETED",
                "file": dict(zip(('file_id','file_name','file_size','file_hash','storage_path','status'), physical[:6])),
                "upload_request": {"upload_id": upload_id, "user_id": session['user_id'], "file_id": physical[0],
                    "workspace_id": session['workspace_id'], "dataset_id": session['dataset_id'],
                    "status": "UPLOADED", "created_at": created_at}}
            complete_session(conn, upload_id, physical[0], result)
            if durable and raw_identity is not None:
                from dataclasses import asdict
                from psycopg.types.json import Jsonb
                conn.execute('SELECT task20_register_source(%s,%s,%s)', (user_id,upload_id,Jsonb(asdict(raw_identity))))
        except Exception as exc:
            # A DB statement error may have aborted this transaction. Roll back, then record failure separately.
            conn.rollback()
            failure = exc
    if failure is not None:
        with locked_upload_session(upload_id) as (conn, session):
            if session and session['completed_at'] is None:
                fail_session(conn, upload_id)
        raise failure
    # Staging survives all incomplete transactions. Cleanup occurs only after durable completion.
    if durable:
        return result  # Durable staging cleanup is a separately approved retention operation.
    try:
        delete_s3_object(object_key)
    except Exception:
        pass  # Completed result remains authoritative; staging lifecycle cleanup can remove leftovers.
    return result
