"""Operation pins and append-only attempts; status recording never executes a job."""
from fastapi import HTTPException
from app.db.database import repository_transaction
from app.db import warehouse_repository as repository
from app.schemas.warehouse import OperationCreate, RunTransition
from app.warehouse.config import require_enabled
from app.warehouse.access import owned_workspace, owned_version, validate_sources


def create_operation(workspace_id, model_id, version_id, user, request: OperationCreate):
    configuration = require_enabled()
    with repository_transaction() as conn:
        _, version = owned_version(conn, workspace_id, model_id, version_id, user, True)
        if version['status'] != 'APPROVED':
            raise HTTPException(409, 'An approved model version is required')
        validate_sources(conn, workspace_id, version_id, user['owner_key'])
        resource = repository.resource(conn, workspace_id, model_id, version_id, request.resource_id)
        if not resource:
            raise HTTPException(404, 'Warehouse resource not found in model version')
        if resource['environment'] != configuration.environment or resource['status'] != 'REGISTERED':
            raise HTTPException(409, 'Resource environment or status is unavailable')
        return repository.create_operation(conn, workspace_id, model_id, version_id, configuration.environment, user['user_id'], request)


def owned_operation(conn, workspace_id, operation_id, user, mutate=False):
    owned_workspace(conn, workspace_id, user, mutate)
    operation = repository.operation(conn, workspace_id, operation_id)
    if not operation:
        raise HTTPException(404, 'Warehouse operation not found in workspace')
    # Pin validation uses current ownership, never the historical operation actor.
    _, version = owned_version(conn, workspace_id, operation['warehouse_model_id'], operation['warehouse_model_version_id'], user, mutate)
    if mutate:
        if operation['environment'] != require_enabled().environment or version['status'] != 'APPROVED':
            raise HTTPException(409, 'Operation version or environment is no longer available')
        validate_sources(conn, workspace_id, version['warehouse_model_version_id'], user['owner_key'])
    return operation


def read_operation(workspace_id, operation_id, user):
    require_enabled()
    with repository_transaction() as conn:
        return owned_operation(conn, workspace_id, operation_id, user)


def new_attempt(workspace_id, operation_id, user):
    require_enabled()
    with repository_transaction() as conn:
        owned_operation(conn, workspace_id, operation_id, user, True)
        return repository.new_attempt(conn, workspace_id, operation_id, user['user_id'])


def read_run(workspace_id, operation_id, run_id, user):
    require_enabled()
    with repository_transaction() as conn:
        owned_operation(conn, workspace_id, operation_id, user)
        row = repository.run(conn, workspace_id, operation_id, run_id)
        if not row:
            raise HTTPException(404, 'Warehouse run not found in operation')
        return row


def record_transition(workspace_id, operation_id, run_id, user, request: RunTransition):
    """Internal lifecycle primitive for future workers/tests, not proof of execution."""
    require_enabled()
    with repository_transaction() as conn:
        owned_operation(conn, workspace_id, operation_id, user, True)
        if not repository.run(conn, workspace_id, operation_id, run_id):
            raise HTTPException(404, 'Warehouse run not found in operation')
        return repository.transition(conn, run_id, request)
