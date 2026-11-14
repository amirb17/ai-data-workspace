"""Production SQL V1 release gate and owner-scoped metadata authority."""
from pathlib import Path
from types import SimpleNamespace
from importlib import import_module
from app.security.public_launch import LaunchSettings, SQL_EXECUTION_ROUTES, permitted

ROOT=Path(__file__).resolve().parents[1]

def test_every_sql_route_default_off_and_exact_handler():
    full=LaunchSettings(enabled=True,analytics_read_enabled=True,
                        sql_catalog_enabled=True,sql_execution_enabled=True)
    before=LaunchSettings(enabled=True,analytics_read_enabled=True,
                          sql_catalog_enabled=True)
    for (method,path),target in SQL_EXECUTION_ROUTES.items():
        module,attr=target.rsplit('.',1)
        endpoint=getattr(import_module(module),attr)
        r=SimpleNamespace(path=path,endpoint=endpoint)
        assert not permitted(r,method,before)
        assert permitted(r,method,full)
        assert not permitted(SimpleNamespace(path=path,endpoint=lambda:None),method,full)
        assert not permitted(r,'GET' if method!='GET' else 'POST',full)


def test_sql_metadata_updates_are_owner_fenced_not_table_grants():
    schema=(ROOT/'migrations/0067_public_sql_metadata_authority.sql').read_text()
    service=(ROOT/'app/db/sql_repository.py').read_text()
    for fn in ('task23_sql_record','task23_sql_save','task23_sql_delete','task23_sql_lock_scope'):
        assert fn in schema and fn in service
    assert "task23_sql_owner(actor,ws)" in schema
    assert "task23_sql_lock_scope(%s,%s)" in service
    assert "ORDER BY dataset_id FOR UPDATE" in schema
    sql_service=(ROOT/'app/services/sql_analytics_service.py').read_text()
    assert 'catalog_service.gather(conn,workspace,user,lock=False)' in sql_service
    assert "u.account_status='ACTIVE'" in schema
    assert "w.workspace_id=ws" in schema
    assert "TO datarise_api" in schema
    for forbidden in ('GRANT INSERT','GRANT UPDATE','GRANT DELETE'):
        assert forbidden not in schema
    assert "GRANT SELECT ON TABLE public.saved_sql_queries,public.sql_query_executions" in schema


def test_terraform_sql_execution_opt_in_is_independent():
    tf=(ROOT/'deployment/terraform/environments/dev/api_ecs_task.tf').read_text()
    assert 'variable "api_enable_sql_execution"' in tf
    assert 'default     = false' in tf
    assert 'DATARISE_PUBLIC_SQL_EXECUTION' in tf
    assert 'var.api_enable_sql_execution || var.api_enable_sql_catalog' in tf
