from app.db.file_repository import (
    create_physical_file,
    find_physical_file_by_hash,
    create_upload_request,
    update_physical_file_storage,
)
from app.db.file_repository import (
    create_physical_file,
    find_physical_file_by_hash,
    create_upload_request,
    update_physical_file_storage,
    create_uploaded_physical_file,
)

from app.storage.s3_service import (
    upload_file_to_s3,
    get_object_metadata,
    calculate_s3_object_hash,
    promote_staging_object_to_raw,
    delete_s3_object,
    build_s3_uri,
)

from app.storage.s3_service import upload_file_to_s3


def register_file(
    user_id: int,
    file_name: str,
    file_size: int,
    file_hash: str,
    local_file_path: str,
):
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
    )

    return {
        "is_duplicate": is_duplicate,
        "file": physical_file,
        "upload_request": upload_request,
    }

def finalize_presigned_upload(
    user_id: int,
    object_key: str,
    expected_file_size: int,
):
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

        physical_file = create_uploaded_physical_file(
            file_name=file_name,
            file_size=metadata["size"],
            file_hash=file_hash,
            storage_path=storage_path,
        )

        delete_s3_object(object_key)

    upload_request = create_upload_request(
        user_id=user_id,
        file_id=physical_file[0],
    )

    return {
        "is_duplicate": is_duplicate,
        "file": physical_file,
        "upload_request": upload_request,
    }