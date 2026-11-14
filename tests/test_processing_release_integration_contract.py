"""Functional release contract: public owner-scoped jobs and explicit async processing."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app.api import product_preview
from app.schemas.product_preview import CapabilitiesResponse
from app.security.public_launch import LaunchSettings


def test_public_capabilities_without_warehouse_reads():
    request=SimpleNamespace(scope={'datarise.public_launch':True})
    launch=LaunchSettings(enabled=True,processing_enabled=True,csv_upload_enabled=True)
    with patch('app.security.public_launch.launch_settings',return_value=launch), \
         patch.object(product_preview.service,'authorize_read') as auth, \
         patch.object(product_preview.service,'capabilities',side_effect=AssertionError('warehouse query forbidden')):
        response=product_preview.capabilities(request,1,1,{'user_id':3})
    auth.assert_called_once()
    assert CapabilitiesResponse.model_validate(response).processing_submission=='ENABLED'
    assert response['warehouse']=='NOT_CONFIGURED'


def test_processing_job_metadata_is_owner_fenced_by_function():
    from app.services import product_preview_service as service
    class Conn:
        def execute(self,sql,args):
            assert 'task24_processing_jobs' in sql and 'FROM processing_jobs' not in sql
            assert args==(3,1,2)
            return SimpleNamespace(fetchall=lambda:[])
    from contextlib import contextmanager
    @contextmanager
    def tx():
        yield Conn()
    with patch.object(service.database,'repository_transaction',tx):
        assert service.jobs(1,2,{'user_id':3})=={'workspace_id':1,'dataset_id':2,'jobs':[]}


def test_frontend_uses_async_jobs_not_legacy_process_or_continue():
    base=Path(__file__).resolve().parents[1]/'frontend/src'
    jobs=(base/'services/api/processingJobs.ts').read_text()
    rules=(base/'services/api/rules.ts').read_text()
    assert "'/files/uploads/'" in jobs and "'/jobs'" in jobs
    assert 'submitStage' in rules and 'readStageJobs' in rules
    assert '/process${scope' not in rules
    assert '/continue${scope' not in rules
    assert 'idempotency_key' in jobs
