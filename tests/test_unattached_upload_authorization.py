"""Unattached sessions remain private to their uploader, outside adoption scope."""
from contextlib import contextmanager
import pytest
from app.services import upload_session_service as service


@pytest.mark.parametrize('actor,allowed',[(1,True),(2,False)])
def test_unattached_completion_replay_remains_uploader_only(monkeypatch,actor,allowed):
    @contextmanager
    def locked(_):
        yield None, {'staging_object_key':'synthetic-staging', 'workspace_id':None, 'dataset_id':None,
                     'user_id':1, 'completed_at':True, 'completion_result':{'synthetic':'original'}}
    monkeypatch.setattr(service,'locked_upload_session',locked)
    monkeypatch.setattr(service,'get_object_metadata',lambda _:pytest.fail('No storage access for completed replay'))
    if allowed:
        assert service.complete_upload_session(1,actor)=={'synthetic':'original'}
    else:
        with pytest.raises(PermissionError): service.complete_upload_session(1,actor)
