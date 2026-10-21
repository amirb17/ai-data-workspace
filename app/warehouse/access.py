"""Workspace ownership is current authorization; actor IDs are provenance only."""
from fastapi import HTTPException
from app.db import warehouse_repository as repository


def owned_workspace(conn, workspace_id, user, mutate=False):
    row = repository.workspace(conn, workspace_id)
    if not row:
        raise HTTPException(404, 'Workspace not found')
    if row['owner'] != user['owner_key']:
        raise HTTPException(403, 'Workspace access denied')
    # Existing workspace APIs allow owned archived metadata reads. New writes
    # require ACTIVE; no status of an existing resource is changed here.
    if mutate and row['status'] != 'ACTIVE':
        raise HTTPException(409, 'Archived workspace metadata is read-only')
    return row


def owned_model(conn, workspace_id, model_id, user, mutate=False):
    owned_workspace(conn, workspace_id, user, mutate)
    row = repository.model(conn, workspace_id, model_id, lock=mutate)
    if not row:
        raise HTTPException(404, 'Warehouse model not found in workspace')
    if mutate and row['status'] == 'ARCHIVED':
        raise HTTPException(409, 'Archived warehouse model is read-only')
    return row


def owned_version(conn, workspace_id, model_id, version_id, user, mutate=False):
    model = owned_model(conn, workspace_id, model_id, user, mutate)
    version = repository.version(conn, workspace_id, model_id, version_id)
    if not version:
        raise HTTPException(404, 'Warehouse version not found in model')
    return model, version


def validate_sources(conn, workspace_id, version_id, owner_key):
    rows = repository.sources(conn, version_id)
    for mapping in rows:
        source = repository.dataset(conn, mapping['source_dataset_id'])
        if not source or source['workspace_id'] != workspace_id or source['owner'] != owner_key:
            raise HTTPException(409, 'Warehouse source membership changed')
        if source['status'] != 'ACTIVE':
            raise HTTPException(409, 'Warehouse source dataset is archived')
    return rows


def public_resource(row):
    return {key: value for key, value in row.items() if key != 'physical_reference'}
