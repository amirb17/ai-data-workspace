"""414D2: session-derived gateway principal; no caller authorization fields."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services.warehouse_sql_authenticated_readiness_service import (
    inspect_authenticated_warehouse_gateway_readiness as check,
)
from app.services.warehouse_sql_gateway_readiness_service import WarehouseGatewayReadiness
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_reliability import reliability_pg
from tests.test_warehouse_query_reservation_publication_links_postgres import (
    linked_pg,setup_attempt,insert_link,
)
from tests.test_warehouse_query_publication_retirement_postgres import retirement_pg


def request(cookie="session-token"):
    return Request({
        "type":"http","method":"GET","path":"/internal",
        "headers":[(b"cookie",f"datarise_session={cookie}".encode())],
    })


def enable_session(monkeypatch,principal):
    from app.auth import service as auth
    from app.auth.config import config
    monkeypatch.setenv("APP_ENV","development")
    monkeypatch.setenv("AUTH_MODE","session")
    monkeypatch.setenv("AUTH_SECRET","s"*64)
    monkeypatch.setenv("AUTH_COOKIE_SECURE","false")
    monkeypatch.setenv("AUTH_FRONTEND_URL","http://localhost:5173")
    monkeypatch.setenv("AUTH_BACKEND_URL","http://localhost:8000")
    received=[]
    def session(token):
        received.append(token)
        return principal
    monkeypatch.setattr(auth,"session",session)
    return received


def test_session_derived_owner_can_inspect_existing_attempt(retirement_pg,monkeypatch):
    f,pub=retirement_pg
    governance,attempt,reservation=setup_attempt(f,pub)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    called=enable_session(monkeypatch,f.pg["owner"])
    result=check(request=request(),workspace_id=f.pg["workspace"],
                 attempt_handle=attempt.attempt_handle)
    assert called==["session-token"]
    assert result==WarehouseGatewayReadiness(
        attempt_handle=attempt.attempt_handle,reservation_id=reservation,
        state="METADATA_CONSISTENT_ONLY",metadata_consistent=True,
    )
    assert not result.execution_authorized
    assert not result.provider_dispatched


def test_missing_session_fails_before_db(retirement_pg,monkeypatch):
    f,pub=retirement_pg
    _,attempt,_=setup_attempt(f,pub)
    called=enable_session(monkeypatch,None)
    with pytest.raises(HTTPException) as exc:
        check(request=request(),workspace_id=f.pg["workspace"],
              attempt_handle=attempt.attempt_handle)
    assert exc.value.status_code==401
    assert called==["session-token"]


def test_different_authenticated_owner_rejected(retirement_pg,monkeypatch):
    f,pub=retirement_pg
    _,attempt,_=setup_attempt(f,pub)
    enable_session(monkeypatch,f.pg["other"])
    from app.db.query_governance_repository import ReservationConflict
    with pytest.raises(ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        check(request=request(),workspace_id=f.pg["workspace"],
              attempt_handle=attempt.attempt_handle)


def test_development_identity_fallback_never_accepted(monkeypatch):
    enable_session(monkeypatch,{"user_id":1,"owner_key":"dev:fake"})
    monkeypatch.setenv("AUTH_MODE","development")
    monkeypatch.setenv("ENABLE_DEV_IDENTITY","true")
    with pytest.raises(HTTPException) as exc:
        check(request=request(),workspace_id=1,attempt_handle="a"*32)
    assert exc.value.status_code==503


def test_unconfigured_session_auth_fails_closed(monkeypatch):
    enable_session(monkeypatch,None)
    monkeypatch.setenv("AUTH_SECRET","short")
    with pytest.raises(HTTPException) as exc:
        check(request=request(),workspace_id=1,attempt_handle="a"*32)
    assert exc.value.status_code==503


def test_untrusted_principal_and_invalid_request_rejected():
    with pytest.raises(ValueError,match="ASGI request"):
        check(request={"owner_key":"attacker"},workspace_id=1,
              attempt_handle="a"*32)
    with pytest.raises(ValueError,match="Positive workspace"):
        check(request=request(),workspace_id=0,attempt_handle="a"*32)


def test_no_new_execution_or_auth_bypass_api():
    source=(Path(__file__).resolve().parents[1]/
        "app/services/warehouse_sql_authenticated_readiness_service.py")
    tree=ast.parse(source.read_text(encoding="utf-8"))
    attributes={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
    assert not attributes.intersection({"execute","claim","admit","recover",
                                        "complete","fail"})
    imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    assert "app.auth.service" not in imports
    assert "app.api.identity" not in imports
    assert "app.services.warehouse_sql_gateway_readiness_service" in imports
