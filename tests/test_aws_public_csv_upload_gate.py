"""Default-off, endpoint-bound public CSV delivery upload release checks."""
from pathlib import Path
from app.main import app
from app.security.public_launch import LaunchSettings, permitted, UPLOAD_ROUTES, http_routes
from app.services.upload_session_service import initiate_upload_session
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[1]


def route(method, path):
    return next(r for r in http_routes(app.router.routes)
                if getattr(r, "path", None) == path and method in getattr(r, "methods", ()))


def test_explicit_upload_routes_only():
    assert set(UPLOAD_ROUTES) == {('POST','/files/initiate'),('POST','/files/complete')}
    off = LaunchSettings(True, ('example.org',), True, True, False)
    on = LaunchSettings(True, ('example.org',), True, True, True)
    for method, path in UPLOAD_ROUTES:
        r = route(method, path)
        assert not permitted(r, method, off)
        assert permitted(r, method, on)
    for method,path in [('POST','/files/upload'),('POST','/files/uploads/{upload_id}/process'),
                        ('POST','/workspaces/{workspace_id}/datasets/{dataset_id}/processing/pending')]:
        r = next((r for r in http_routes(app.router.routes)
                  if getattr(r,'path',None)==path and method in getattr(r,'methods',())),None)
        if r is not None:
            assert not permitted(r, method, on)


def test_upload_bounds_before_object_signing():
    with patch('app.services.upload_session_service.validate_upload_context'):
        for size, mime, name in [(0,'text/csv','a.csv'),
            (5*1024*1024+1,'text/csv','a.csv'),(1,'application/octet-stream','a.csv'),
            (1,'text/csv','a.xlsx'),(1,'text/csv','../a.csv')]:
            with pytest.raises(ValueError):
                initiate_upload_session(1,1,2,name,size,mime)


def test_task_role_and_runtime_gate_default_off():
    task = (ROOT/'deployment/terraform/environments/dev/api_ecs_task.tf').read_text()
    infra = (ROOT/'deployment/terraform/environments/dev/api_s3_upload.tf').read_text()
    assert 'variable "enable_api_s3_csv_upload"' in infra
    assert 'default     = false' in infra
    assert 'arn:aws:s3:::' in infra
    assert 'task_role_arn' in task
    assert '{ name = "S3_BUCKET_NAME", value = var.api_s3_upload_bucket }' in task
    assert '{ name = "AWS_REGION", value = var.api_s3_upload_region }' in task
    assert '{ name = "DATARISE_PUBLIC_CSV_UPLOAD", value = tostring(var.enable_api_s3_csv_upload) }' in task
