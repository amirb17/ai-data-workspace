"""Public Ask AI routes are individually bound and default to denied."""
from importlib import import_module
from types import SimpleNamespace
import pytest
from app.security.public_launch import ASK_AI_ROUTES, LaunchSettings, W, permitted

@pytest.mark.parametrize("method,path,target",[
    (method,path,target) for (method,path),target in ASK_AI_ROUTES.items()
])
def test_ask_ai_routes_require_exact_opt_in_and_endpoint(method,path,target):
    module,attr=target.rsplit(".",1)
    endpoint=getattr(import_module(module),attr)
    route=SimpleNamespace(path=path,endpoint=endpoint)
    assert not permitted(route,method,None)
    assert not permitted(route,method,LaunchSettings(enabled=True))
    enabled=LaunchSettings(enabled=True,processing_enabled=True,
        analytics_read_enabled=True,model_read_enabled=True,ask_ai_enabled=True)
    assert permitted(route,method,enabled)
    assert not permitted(SimpleNamespace(path=path,endpoint=lambda: None),method,enabled)
    assert not permitted(route,'DELETE',enabled)

@pytest.mark.parametrize("method,path",[
    ('POST',W+'/warehouse/queries'),
    ('GET',W+'/questions'),
    ('DELETE',W+'/questions/answers/{answer_id}'),
    ('POST',W+'/sql/execute'),
    ('POST',W+'/relationships/discover'),
    ('POST',W+'/questions/answers'),
])
def test_ask_ai_opt_in_does_not_authorize_unrelated_routes(method,path):
    enabled=LaunchSettings(enabled=True,ask_ai_enabled=True)
    assert not permitted(SimpleNamespace(path=path,endpoint=lambda: None),method,enabled)
