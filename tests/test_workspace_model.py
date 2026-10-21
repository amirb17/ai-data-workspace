"""Model summary reuses authoritative scope/freshness; it never generates or writes data."""
from contextlib import contextmanager
from copy import deepcopy
import pytest
from fastapi import HTTPException
from app.api import workspace_model as model


def test_summary_projection_and_read_only_transaction(monkeypatch):
    calls = []
    class Connection:
        def execute(self, statement):
            calls.append(statement)
    @contextmanager
    def transaction():
        yield Connection()
    monkeypatch.setattr(model, 'repository_transaction', transaction)
    monkeypatch.setattr(model, 'owned_workspace', lambda ws, user: calls.append(('owned', ws, user['owner_key'])))
    monkeypatch.setattr(model, 'get_datasets_by_workspace', lambda ws: [(2, 'Orders', '', 'legacy', 'ACTIVE', None, None, ws)])
    profile = dict(freshness='READY', profile_ready=True, profile_id=3, current_state_id=4, state_version=1, profile_version=1,
                   profile_timestamp=None, summary={'row_count': 7, 'column_count': 1, 'business_key': ['id'], 'event_time_column': None}, columns=[dict(original_name='id',
                   canonical_type='TEXT', authoritative_business_key=True, null_ratio=0, distinct_count=7,
                   sensitivity_hints=[], min_value='PRIVATE VALUE')])
    meaning = dict(status='READY', source_profile_id=3, source_state_id=4, suggestion=dict(provider='PRIVATE PROVIDER',
                    reasoning=dict(entity={'primary': {'label': 'Orders', 'rationale': 'Stored meaning'}}, columns=[], warnings=['hidden extra'])))
    before = deepcopy((profile, meaning))
    monkeypatch.setattr(model, 'read_profile', lambda *args: profile)
    monkeypatch.setattr(model, 'read_understanding', lambda *args: meaning)
    result = model.model_summary(1, {'owner_key': 'legacy'})
    assert calls == ['SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY', ('owned', 1, 'legacy')]
    assert result['workspace_id'] == 1 and len(result['datasets']) == 1
    assert result['datasets'][0]['profile']['summary']['row_count'] == 7
    assert 'PRIVATE' not in str(result)
    assert (profile, meaning) == before


def test_foreign_workspace_fails_before_dataset_reads(monkeypatch):
    @contextmanager
    def transaction():
        class Connection:
            def execute(self, statement): pass
        yield Connection()
    def denied(*args): raise HTTPException(403, 'Workspace access denied')
    def forbidden(*args): pytest.fail('Must not read foreign dataset metadata')
    monkeypatch.setattr(model, 'repository_transaction', transaction)
    monkeypatch.setattr(model, 'owned_workspace', denied)
    monkeypatch.setattr(model, 'get_datasets_by_workspace', forbidden)
    with pytest.raises(HTTPException) as error:
        model.model_summary(1, {'owner_key': 'other'})
    assert error.value.status_code == 403


def test_pg_model_summary_preserves_profile_and_scope(profiled):
    # The shared fixture lives in the full recovered legacy suite snapshot.
    from tests.test_semantic_profiles import refresh
    from app.services import dataset_profile_service
    from app.api.workspaces import owned_workspace
    f = profiled
    db = f[0][0]
    workspace, user, dataset = db[2], db[1], db[3]
    refresh(f)
    before = dataset_profile_service.read_profile(workspace, dataset, user)
    result = model.model_summary(workspace, user)
    entry = next(r for r in result['datasets'] if r['dataset']['id'] == dataset)
    assert entry['profile']['summary'] == {key: before['summary'][key] for key in ('row_count', 'column_count', 'business_key', 'event_time_column')}
    assert entry['meaning']['status'] == 'NOT_GENERATED'
    assert dataset_profile_service.read_profile(workspace, dataset, user) == before
    with pytest.raises(HTTPException):
        model.model_summary(workspace, {**user, 'owner_key': 'foreign'})


# Import only during DB-enabled snapshot runs; the working tree intentionally has deleted legacy tests.
try:
    from tests.test_semantic_profiles import pg, deliveries, pipeline, append_pipeline, profiled
except ModuleNotFoundError:
    @pytest.fixture
    def profiled():
        pytest.skip('Legacy fixtures are available in the complete temporary test snapshot')
