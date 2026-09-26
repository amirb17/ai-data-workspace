from app.db.dataset_repository import (
    create_dataset,
    get_dataset_by_workspace_and_name,
    get_dataset_version_by_schema,
    create_next_dataset_version,
)


def get_or_create_dataset(
    workspace_id: int,
    dataset_name: str,
    description: str | None = None,
    owner: str | None = None,
):
    existing_dataset = get_dataset_by_workspace_and_name(
        workspace_id=workspace_id,
        dataset_name=dataset_name,
    )

    if existing_dataset is not None:
        return {
            "workspace_id": workspace_id,
            "dataset_id": existing_dataset[0],
            "dataset_name": existing_dataset[1],
            "description": existing_dataset[2],
            "owner": existing_dataset[3],
            "status": existing_dataset[4],
            "created": False,
        }

    dataset = create_dataset(
        workspace_id=workspace_id,
        dataset_name=dataset_name,
        description=description,
        owner=owner,
    )

    return {
        "workspace_id": workspace_id,
        "dataset_id": dataset[0],
        "dataset_name": dataset[1],
        "description": dataset[2],
        "owner": dataset[3],
        "status": dataset[4],
        "created": True,
    }


def resolve_dataset_version(
    workspace_id: int,
    dataset_name: str,
    schema_hash: str,
):
    dataset = get_dataset_by_workspace_and_name(
        workspace_id=workspace_id,
        dataset_name=dataset_name,
    )

    if dataset is None:
        raise ValueError(
            f"Dataset '{dataset_name}' not found "
            f"in workspace {workspace_id}"
        )

    dataset_id = dataset[0]

    existing_version = get_dataset_version_by_schema(
        dataset_id=dataset_id,
        schema_hash=schema_hash,
    )

    if existing_version is not None:
        return {
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "dataset_version_id": existing_version[0],
            "version_number": existing_version[2],
            "schema_hash": existing_version[3],
            "created": False,
        }

    new_version = create_next_dataset_version(
        dataset_id=dataset_id,
        schema_hash=schema_hash,
    )

    return {
        "workspace_id": workspace_id,
        "dataset_id": dataset_id,
        "dataset_version_id": new_version[0],
        "version_number": new_version[2],
        "schema_hash": new_version[3],
        "created": True,
    }