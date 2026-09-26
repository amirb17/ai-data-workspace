from app.db.file_repository import (
    create_physical_file,
    find_physical_file_by_hash,
    create_upload_request,
    update_physical_file_storage,
    create_uploaded_physical_file,
)
from app.db.dataset_repository import get_dataset_by_workspace
from app.storage.s3_service import (
    upload_file_to_s3,
    get_object_metadata,
    calculate_s3_object_hash,
    promote_staging_object_to_raw,
    delete_s3_object,
    build_s3_uri,
)


def validate_upload_context(
    user_id: int,
    workspace_id: int | None,
    dataset_id: int | None,
) -> None:
    """
    Enforce that workspace/dataset context is either fully provided
    or fully absent, and that the dataset actually belongs to the
    workspace when both are given.

    Historical uploads may legitimately have no logical context, but
    a request must never mix "some context" with "no context".
    """

    if (workspace_id is None) != (dataset_id is None):
        raise ValueError(
            "workspace_id and dataset_id must both be provided, "
            "or both omitted"
        )

    if workspace_id is None or dataset_id is None:
        return

    dataset = get_dataset_by_workspace(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )

    if dataset is None:
        raise ValueError(
            f"Dataset {dataset_id} does not belong to workspace {workspace_id}"
        )


def _staging_prefix(user_id: int) -> str:
    return f"staging/user-{user_id}/"


def _validate_staging_object_key(user_id: int, object_key: str) -> None:
    """
    Ensure a presigned-upload completion can only ever reference the
    caller's own staging object. Without this, a caller could pass an
    arbitrary object_key (e.g. an existing raw/<hash>/... key) and
    trigger deletion of unrelated, already-promoted objects.
    """

    prefix = _staging_prefix(user_id)

    if ".." in object_key or not object_key.startswith(prefix):
        raise ValueError(
            "object_key must reference the caller's own staging upload"
        )


def register_file(
    user_id: int,
    file_name: str,
    file_size: int,
    file_hash: str,
    local_file_path: str,
    workspace_id: int | None = None,
    dataset_id: int | None = None,
):
    validate_upload_context(
        user_id=user_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )

    existing_file = find_physical_file_by_hash(file_hash)

    if existing_file:
        physical_file = existing_file
        is_duplicate = True

    else:
        is_duplicate = False

        physical_file = create_physical_file(
            file_name=file_name,
            file_size=file_size,
            file_hash=file_hash,
        )

        file_id = physical_file[0]

        try:
            s3_uri = upload_file_to_s3(
                local_file_path=local_file_path,
                file_name=file_name,
                file_hash=file_hash,
            )

            physical_file = update_physical_file_storage(
                file_id=file_id,
                storage_path=s3_uri,
                status="UPLOADED",
            )

        except Exception:
            update_physical_file_storage(
                file_id=file_id,
                storage_path=None,
                status="FAILED",
            )
            raise

    upload_request = create_upload_request(
        user_id=user_id,
        file_id=physical_file[0],
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )

    return {
        "is_duplicate": is_duplicate,
        "file": physical_file,
        "upload_request": upload_request,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
    }


def finalize_presigned_upload(
    user_id: int,
    object_key: str,
    expected_file_size: int,
    workspace_id: int | None = None,
    dataset_id: int | None = None,
):
    validate_upload_context(
        user_id=user_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )

    _validate_staging_object_key(
        user_id=user_id,
        object_key=object_key,
    )

    metadata = get_object_metadata(object_key)

    if metadata["size"] != expected_file_size:
        raise ValueError(
            f"File size mismatch. "
            f"Expected {expected_file_size}, "
            f"received {metadata['size']}"
        )

    file_hash = calculate_s3_object_hash(object_key)

    existing_file = find_physical_file_by_hash(file_hash)

    if existing_file:
        physical_file = existing_file
        is_duplicate = True

        delete_s3_object(object_key)

    else:
        is_duplicate = False

        file_name = object_key.rsplit("/", 1)[-1]

        raw_object_key = promote_staging_object_to_raw(
            staging_object_key=object_key,
            file_hash=file_hash,
            file_name=file_name,
        )

        storage_path = build_s3_uri(raw_object_key)

        physical_file, was_inserted = create_uploaded_physical_file(
            file_name=file_name,
            file_size=metadata["size"],
            file_hash=file_hash,
            storage_path=storage_path,
        )

        # A concurrent completion for identical bytes may have won the
        # insert race; treat this request as a duplicate in that case.
        is_duplicate = not was_inserted

        delete_s3_object(object_key)

    upload_request = create_upload_request(
        user_id=user_id,
        file_id=physical_file[0],
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )

    return {
        "is_duplicate": is_duplicate,
        "file": physical_file,
        "upload_request": upload_request,
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
    }
