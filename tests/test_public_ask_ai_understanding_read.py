"""Dataset AI understanding read routes are opt-in; generation remains denied."""
from importlib import import_module
from types import SimpleNamespace
import pytest
from app.security.public_launch import (
    ASK_AI_UNDERSTANDING_READ_ROUTES, LaunchSettings, D, permitted
)

@pytest.mark.parametrize("method,path,target",[
    (method,path,target) for (method,path),target in ASK_AI_UNDERSTANDING_READ_ROUTES.items()
])
def test_exact_read_routes_require_ask_ai_gate(method,path,target):
    module,name=target.rsplit(".",1)
    endpoint=getattr(import_module(module),name)
    route=SimpleNamespace(path=path,endpoint=endpoint)
    assert not permitted(route,method,None)
    assert not permitted(route,method,LaunchSettings(enabled=True))
    settings=LaunchSettings(enabled=True,processing_enabled=True,model_read_enabled=True,
                            analytics_read_enabled=True,ask_ai_enabled=True)
    assert permitted(route,method,settings)
    assert not permitted(SimpleNamespace(path=path,endpoint=lambda:None),method,settings)
    assert not permitted(route,"POST",settings)

def test_generation_write_route_remains_disallowed():
    route=SimpleNamespace(path=D+"/semantic-understanding/generate",
                          endpoint=import_module("app.api.dataset_understanding").generate)
    settings=LaunchSettings(enabled=True,processing_enabled=True,model_read_enabled=True,
                            analytics_read_enabled=True,ask_ai_enabled=True)
    assert not permitted(route,"POST",settings)
