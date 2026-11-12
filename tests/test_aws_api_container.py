"""Offline tests for the dedicated fail-closed API container build."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / 'deployment/docker/Dockerfile.api'


def test_api_image_is_separate_from_worker_and_uses_immutable_build_inputs():
    content = DOCKER.read_text()
    assert 'FROM ${API_BASE_IMAGE}' in content
    assert 'verify_lock.py --base "$API_BASE_IMAGE"' in content
    assert 'SOURCE_REVISION' in content and 'RUNTIME_LOCK_SHA' in content
    assert 'COPY --chown=10002:10002 app ./app' in content
    assert 'USER 10002' in content
    assert 'app.main:app' in content
    assert 'app.processing_worker' not in content
    assert 'warehouse_local_live_api' not in content


def test_public_launch_and_cloud_processing_are_disabled_or_fail_closed():
    content = DOCKER.read_text()
    for item in [
        'APP_ENV=production', 'AUTH_MODE=session',
        'AUTH_COOKIE_SECURE=true', 'ENABLE_DEV_IDENTITY=false',
        'DATARISE_PUBLIC_LAUNCH_MODE=csv-readonly',
        'DATARISE_PROCESSING_DEPLOYMENT_APPROVED=false',
        'DATARISE_PROCESSING_READERS=false',
        'DATARISE_DISPATCH_APPROVED=false',
        'WAREHOUSE_ENABLED=false', 'PGSSLMODE=verify-full',
        '--no-proxy-headers',
    ]:
        assert item in content
    assert 'COPY .env ' not in content
    assert not re.search(r'(?m)^\s*RUN\s+.*(?:aws\s|terraform\s)', content)


def test_readiness_document_disallows_unreviewed_public_release():
    content = (ROOT / 'docs/aws_api_container_readiness.md').read_text()
    assert "scope['scheme']=='https'" in content
    assert 'Organizations SCP' in content
    assert 'VITE_API_BASE_URL' in content
