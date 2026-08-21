from app.db.file_repository import (
    create_physical_file,
    find_physical_file_by_hash,
    create_upload_request,
    update_physical_file_storage,
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