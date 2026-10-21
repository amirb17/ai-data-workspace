"""Backend-only resource registration; never provisions or exposes physical IDs."""
from fastapi import HTTPException
from app.db.database import repository_transaction
from app.db import warehouse_repository as repository
from app.schemas.warehouse import ResourceRegistration
from app.warehouse.config import require_enabled
from app.warehouse.access import owned_version, public_resource, validate_sources


def register_resource(workspace_id, model_id, version_id, user, registration: ResourceRegistration):
    """Trusted backend call only; deliberately not connected to an API route."""
    configuration = require_enabled()
    if registration.environment != configuration.environment:
        raise ValueError('Resource environment does not match application environment')
    with repository_transaction() as conn:
        _, version = owned_version(conn, workspace_id, model_id, version_id, user, True)
        if version['status'] != 'APPROVED':
            raise HTTPException(409, 'Resource registration requires an approved version')
        validate_sources(conn, workspace_id, version_id, user['owner_key'])
        row = repository.register_resource(conn, workspace_id, model_id, version_id, user['user_id'], registration)
        return public_resource(row)


def read_resource(workspace_id, model_id, version_id, resource_id, user):
    require_enabled()
    with repository_transaction() as conn:
        owned_version(conn, workspace_id, model_id, version_id, user)
        row = repository.resource(conn, workspace_id, model_id, version_id, resource_id)
        if not row:
            raise HTTPException(404, 'Warehouse resource not found in model version')
        return public_resource(row)


def list_resources(workspace_id, model_id, version_id, user):
    require_enabled()
    with repository_transaction() as conn:
        owned_version(conn, workspace_id, model_id, version_id, user)
        return [public_resource(row) for row in repository.resources(conn, workspace_id, model_id, version_id)]
