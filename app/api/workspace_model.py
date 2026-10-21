"""Compact, read-only model metadata using the existing ownership/freshness services."""
from fastapi import APIRouter, Depends
from app.api.identity import get_current_user
from app.api.processing_errors import public_call
from app.api.workspaces import owned_workspace, dataset_payload
from app.db.database import repository_transaction
from app.db.dataset_repository import get_datasets_by_workspace
from app.services.dataset_profile_service import read_profile
from app.services.dataset_understanding_service import read_understanding

router = APIRouter(prefix='/workspaces/{workspace_id}', tags=['Workspace model'])


def model_summary(workspace_id, user):
    with repository_transaction() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        owned_workspace(workspace_id, user)
        values = []
        for row in get_datasets_by_workspace(workspace_id):
            dataset = dataset_payload(row)
            profile = read_profile(workspace_id, dataset['dataset_id'], user)
            meaning = read_understanding(workspace_id, dataset['dataset_id'], user)
            # Internal provider/configuration metadata, raw values and paths are omitted.
            public_profile = {key: profile[key] for key in ('freshness', 'profile_ready', 'profile_id',
                'current_state_id', 'state_version', 'profile_version', 'profile_timestamp', 'summary')}
            public_profile['summary'] = {key: profile['summary'][key] for key in
                ('row_count', 'column_count', 'business_key', 'event_time_column')} if profile['summary'] else None
            public_profile['columns'] = [{key: column[key] for key in ('original_name', 'canonical_type',
                'authoritative_business_key', 'null_ratio', 'distinct_count', 'sensitivity_hints')}
                for column in profile['columns']]
            public_meaning = {key: meaning[key] for key in ('status', 'source_profile_id', 'source_state_id')}
            suggestion = meaning['suggestion']
            public_meaning['suggestion'] = {'reasoning': {
                'entity': {'primary': {key: suggestion['reasoning']['entity']['primary'][key]
                    for key in ('label', 'rationale')}},
                'columns': [{key: column[key] for key in ('column_name', 'business_meaning',
                    'suggested_role', 'warnings')} for column in suggestion['reasoning']['columns']]
            }} if suggestion else None
            values.append({'dataset': {'id': dataset['dataset_id'], 'name': dataset['dataset_name'],
                'description': dataset['description'] or '', 'status': dataset['status'],
                'updatedAt': dataset['updated_at']}, 'profile': public_profile, 'meaning': public_meaning})
        return {'workspace_id': workspace_id, 'datasets': values}


@router.get('/model-summary')
def read(workspace_id: int, user: dict = Depends(get_current_user)):
    return public_call(lambda: model_summary(workspace_id, user))
