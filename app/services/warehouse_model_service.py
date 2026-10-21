"""Logical metadata lifecycle only; no pipeline or warehouse execution."""
from fastapi import HTTPException
from app.db.database import repository_transaction
from app.db import warehouse_repository as repository
from app.schemas.warehouse import ModelCreate, VersionCreate, SourceMapping
from app.warehouse.config import require_enabled
from app.warehouse.access import owned_workspace, owned_model, owned_version, validate_sources


def list_models(workspace_id, user):
    require_enabled()
    with repository_transaction() as conn:
        owned_workspace(conn, workspace_id, user)
        return repository.models(conn, workspace_id)


def read_model(workspace_id, model_id, user):
    require_enabled()
    with repository_transaction() as conn:
        return owned_model(conn, workspace_id, model_id, user)


def create_model(workspace_id, user, request: ModelCreate):
    require_enabled()
    with repository_transaction() as conn:
        owned_workspace(conn, workspace_id, user, True)
        return repository.create_model(conn, workspace_id, user['user_id'], request)


def create_version(workspace_id, model_id, user, request: VersionCreate):
    require_enabled()
    with repository_transaction() as conn:
        owned_model(conn, workspace_id, model_id, user, True)
        return repository.create_version(conn, workspace_id, model_id, user['user_id'], request)


def read_version(workspace_id, model_id, version_id, user):
    require_enabled()
    with repository_transaction() as conn:
        _, version = owned_version(conn, workspace_id, model_id, version_id, user)
        mappings = validate_sources(conn, workspace_id, version_id, user['owner_key'])
        return {**version, 'sources': mappings}


def add_source(workspace_id, model_id, version_id, user, request: SourceMapping):
    require_enabled()
    with repository_transaction() as conn:
        _, version = owned_version(conn, workspace_id, model_id, version_id, user, True)
        if version['status'] != 'DRAFT':
            raise HTTPException(409, 'Only draft source mappings can change')
        source = repository.dataset(conn, request.source_dataset_id)
        if not source or source['workspace_id'] != workspace_id or source['owner'] != user['owner_key']:
            raise HTTPException(404, 'Source dataset not found in workspace')
        if source['status'] != 'ACTIVE':
            raise HTTPException(409, 'Source dataset is archived')
        return repository.add_source(conn, workspace_id, model_id, version_id, request)


def approve_version(workspace_id, model_id, version_id, user):
    require_enabled()
    with repository_transaction() as conn:
        _, version = owned_version(conn, workspace_id, model_id, version_id, user, True)
        if version['status'] != 'DRAFT':
            raise HTTPException(409, 'Only a draft version can be approved')
        rows = validate_sources(conn, workspace_id, version_id, user['owner_key'])
        if not any(row['purpose'] == 'PRIMARY' for row in rows):
            raise HTTPException(409, 'A primary source is required')
        if repository.modeling_available(conn):
            from app.services.warehouse_modeling_service import check_configuration
            check_configuration(conn, workspace_id, model_id, version_id)
        return repository.approve(conn, model_id, version_id, user['user_id'])


def remove_source(workspace_id, model_id, version_id, mapping_id, user):
    require_enabled()
    with repository_transaction() as conn:
        _, version = owned_version(conn, workspace_id, model_id, version_id, user, True)
        if version['status'] != 'DRAFT':
            raise HTTPException(409, 'Only draft source mappings can change')
        if not repository.remove_source(conn, version_id, mapping_id):
            raise HTTPException(404, 'Source mapping not found in version')
        return {'removed': True}


def archive_model(workspace_id, model_id, user):
    require_enabled()
    with repository_transaction() as conn:
        owned_model(conn, workspace_id, model_id, user, True)
        return repository.archive(conn, model_id)
