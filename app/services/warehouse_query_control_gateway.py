"""Explicit authenticated-query control boundary for the FastAPI process.

Only the query-control login is allowed here; provider/attester/accounting
DB credentials belong exclusively to separately hosted workers. No client
or cloud credentials are constructed here and no live query execution switch
is introduced. Absent configuration returns HTTP 503.
"""
from __future__ import annotations

from contextlib import contextmanager
import os

from fastapi import HTTPException, Request

from app.db.warehouse_canonical_query_connection import (
    CanonicalWarehouseQueryControlConnection,
)


@contextmanager
def query_control_boundary(request: Request):
    if not isinstance(request, Request):
        raise HTTPException(503, "Warehouse query control unavailable")
    dsn = os.environ.get("DATARISE_WAREHOUSE_CONTROL_DSN")
    if not dsn:
        raise HTTPException(503, "Warehouse query control unavailable")
    # This constructor requires the literal query-control login and the
    # correct database. The first connection additionally audits the role's
    # live effective privileges; it cannot silently fall back to DATABASE_URL.
    try:
        factory = CanonicalWarehouseQueryControlConnection(dsn=dsn)
        with factory.boundary():
            yield
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(503, "Warehouse query control unavailable") from None
