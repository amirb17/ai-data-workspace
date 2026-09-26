from app.db.workspace_repository import (
    create_workspace,
    get_workspace_by_owner_and_name,
)


def create_or_get_workspace(
    workspace_name: str,
    owner: str,
    description: str | None = None,
):
    if not owner:
        raise ValueError("owner is required to create or look up a workspace")

    existing_workspace = get_workspace_by_owner_and_name(
        owner=owner,
        workspace_name=workspace_name,
    )

    if existing_workspace is not None:
        return {
            "workspace_id": existing_workspace[0],
            "workspace_name": existing_workspace[1],
            "description": existing_workspace[2],
            "owner": existing_workspace[3],
            "status": existing_workspace[4],
            "created": False,
        }

    workspace, was_inserted = create_workspace(
        workspace_name=workspace_name,
        description=description,
        owner=owner,
    )

    return {
        "workspace_id": workspace[0],
        "workspace_name": workspace[1],
        "description": workspace[2],
        "owner": workspace[3],
        "status": workspace[4],
        "created": was_inserted,
    }
