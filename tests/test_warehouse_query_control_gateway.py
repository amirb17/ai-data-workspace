"""Fail-closed control-only query status gateway tests."""
import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services.warehouse_query_control_gateway import query_control_boundary


def _request():
    return Request({"type":"http","method":"GET","path":"/","headers":[]})


def test_status_gateway_remains_disabled_without_explicit_dsn():
    with patch.dict(os.environ,{},clear=True):
        with pytest.raises(HTTPException) as failure:
            with query_control_boundary(_request()):
                pass
    assert failure.value.status_code==503


def test_status_gateway_refuses_provider_worker_dsn():
    with patch.dict(os.environ,{
        "DATARISE_WAREHOUSE_CONTROL_DSN":
            "host=localhost dbname=ai_data_workspace user=datarise_wh_submit"
    }):
        with pytest.raises(HTTPException) as failure:
            with query_control_boundary(_request()):
                pass
    assert failure.value.status_code==503


def test_status_gateway_refuses_admin_dsn():
    with patch.dict(os.environ,{
        "DATARISE_WAREHOUSE_CONTROL_DSN":
            "host=localhost dbname=ai_data_workspace user=postgres"
    }):
        with pytest.raises(HTTPException) as failure:
            with query_control_boundary(_request()):
                pass
    assert failure.value.status_code==503
