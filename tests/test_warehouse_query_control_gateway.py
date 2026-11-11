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


def test_control_connection_constructor_validates_identity_before_sql():
    from app.db.warehouse_canonical_query_connection import (
        CanonicalWarehouseQueryControlConnection,
    )
    for login in ("postgres", "datarise_wh_submit", "datarise_wh_account"):
        with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
            CanonicalWarehouseQueryControlConnection(
                dsn=f"host=localhost dbname=ai_data_workspace user={login}"
            )
    # Eager identity validation is pure configuration parsing; no database
    # connection is made until __call__ runs inside a repository transaction.
    factory = CanonicalWarehouseQueryControlConnection(
        dsn="host=localhost dbname=ai_data_workspace user=datarise_wh_control"
    )
    assert callable(factory)
