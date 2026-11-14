"""Profile release is independent of Analytics, SQL execution and warehouse grants."""
from pathlib import Path
from types import SimpleNamespace
from app.security.public_launch import LaunchSettings, PROFILE_REFRESH_ROUTES, permitted


def test_public_profile_refresh_requires_explicit_opt_in():
    from app.api.dataset_profiles import refresh
    route=SimpleNamespace(path='/workspaces/{workspace_id}/datasets/{dataset_id}/profile/refresh', endpoint=refresh)
    assert not permitted(route,'POST',LaunchSettings())
    assert not permitted(route,'POST',LaunchSettings(enabled=True,analytics_read_enabled=True))
    assert permitted(route,'POST',LaunchSettings(enabled=True,analytics_read_enabled=True,profile_refresh_enabled=True))
    assert not permitted(route,'GET',LaunchSettings(enabled=True,profile_refresh_enabled=True))
    assert not permitted(SimpleNamespace(path=route.path,endpoint=lambda:None),'POST',
                         LaunchSettings(enabled=True,profile_refresh_enabled=True))


def test_profile_metadata_only_published_through_authority():
    root=Path(__file__).resolve().parents[1]
    service=(root/'app/services/dataset_profile_service.py').read_text()
    sql=(root/'migrations/0066_profile_refresh_authority.sql').read_text()
    for fn in ('task22_profile_begin','task22_profile_publish','task22_profile_fail'):
        assert fn in service and fn in sql
    assert 'repository.publish(conn,dataset,run,summary,evidence)' not in service
    assert 'GRANT SELECT ON TABLE public.approved_rule_policies TO datarise_api' in sql
    assert 'GRANT UPDATE ON datasets' not in sql
    assert 'GRANT INSERT ON semantic_profiles' not in sql
    assert 'SECURITY DEFINER' in sql
    assert "task21_analytics_owner(actor,w,d)" in sql
    assert "profile_summary->>'row_count'" in sql


def test_profile_env_switch_defaults_false():
    tf=(Path(__file__).resolve().parents[1]/
        'deployment/terraform/environments/dev/api_ecs_task.tf').read_text()
    assert 'variable "api_enable_profile_refresh"' in tf
    assert 'DATARISE_PUBLIC_PROFILE_REFRESH' in tf
