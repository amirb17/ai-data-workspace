"""Warehouse catalog is a scoped metadata projection, not a BigQuery reader."""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app.services import warehouse_catalog_service as service


FACT = {
    "model_type": "FACT",
    "schema_version": 1,
    "grain": {"description": "One order line", "identity_columns": ["line_id"]},
    "event_time": None,
    "measures": ["amount"],
    "attributes": ["product_id"],
    "identity_null_policy": "REJECT",
}
DIMENSION = {
    "model_type": "DIMENSION",
    "schema_version": 1,
    "business_keys": ["product_id"],
    "scd_type": "SCD_TYPE_2",
    "tracked_attributes": ["category"],
    "nonhistorical_attributes": [],
    "effective_time": {"column": "changed_at", "interpretation": "UTC", "timezone": None},
    "history_coverage": "FROM_FIRST_OBSERVED_EVENT",
    "missing_member_policy": "REJECT",
    "null_business_key_policy": "REJECT",
    "snapshot_absence_policy": "KEEP",
}


def row(model_id, config):
    columns = [
        {"name": name, "data_type": kind, "nullable": None}
        for name, kind in (
            [("line_id", "STRING"), ("amount", "DECIMAL"), ("product_id", "STRING")]
            if config["model_type"] == "FACT"
            else [("product_id", "STRING"), ("category", "STRING"), ("changed_at", "TIMESTAMP")]
        )
    ]
    return {
        "warehouse_model_id": model_id,
        "display_name": "Sales" if model_id == 1 else "Products",
        "warehouse_model_version_id": model_id * 10,
        "version_number": 1,
        "configuration": config,
        "source_schema": {"schema_version": 1, "columns": columns},
    }


class Cursor:
    def __init__(self, datasets):
        self.datasets = datasets
        self.sql = ""
        self.args = ()

    def execute(self, sql, args):
        self.sql = sql
        self.args = args
        return self

    def fetchall(self):
        sql = self.sql
        if "FROM warehouse_models m" in sql:
            assert self.args == (4,)
            assert "v.status = 'APPROVED'" in sql
            assert "m.status = 'APPROVED'" in sql
            return [row(1, FACT), row(2, DIMENSION)]
        if "FROM warehouse_model_sources s" in sql:
            assert self.args == ("owner:4", 4)
            return self.datasets
        if "FROM warehouse_resources" in sql:
            assert self.args == (4,)
            return [{"warehouse_model_version_id": 10, "resource_count": 1}]
        raise AssertionError("Unexpected database query")


class Connection:
    def __init__(self, datasets):
        self.datasets = datasets

    def cursor(self, *, row_factory):
        assert row_factory is not None
        return Cursor(self.datasets)


def setup(monkeypatch, datasets=None):
    calls = []
    if datasets is None:
        datasets = [
            {"warehouse_model_version_id": 10, "source_dataset_id": 91, "dataset_name": "Orders", "purpose": "PRIMARY"},
            {"warehouse_model_version_id": 20, "source_dataset_id": 92, "dataset_name": "Products", "purpose": "PRIMARY"},
        ]

    @contextmanager
    def transaction():
        calls.append("tx")
        yield Connection(datasets)

    monkeypatch.setattr(service, "require_enabled", lambda: calls.append("enabled"))
    monkeypatch.setattr(service, "repository_transaction", transaction)
    monkeypatch.setattr(service, "owned_workspace", lambda conn, ws, user: calls.append(("scope", ws, user["owner_key"])))
    return calls


def test_catalog_projects_approved_fact_and_scd2_metadata(monkeypatch):
    calls = setup(monkeypatch)
    result = service.snapshot(4, {"owner_key": "owner:4"})
    assert calls[:3] == ["enabled", "tx", ("scope", 4, "owner:4")]
    assert result["workspace_id"] == 4
    assert result["relationships"] == []
    assert result["relationship_status"] == "NOT_MODELED"
    assert result["query_execution_enabled"] is False
    fact, dimension = result["models"]
    assert fact["model_type"] == "FACT"
    assert fact["grain"] == "One order line"
    assert fact["identity_columns"] == ["line_id"]
    assert fact["measures"] == ["amount"]
    assert fact["columns"][1] == {"name": "amount", "type": "DECIMAL", "role": "MEASURE"}
    assert dimension["model_type"] == "DIMENSION"
    assert dimension["scd_type"] == "SCD_TYPE_2"
    assert dimension["event_time_column"] == "changed_at"
    assert fact["resource_registered"] is True
    assert dimension["resource_registered"] is False
    assert all(not x["queryable"] and x["serving_status"] == "NOT_VERIFIED" for x in result["models"])
    assert "project" not in str(result)
    assert "physical_reference" not in str(result)


def test_catalog_does_not_display_unowned_or_missing_primary_source(monkeypatch):
    setup(monkeypatch, datasets=[
        {"warehouse_model_version_id": 20, "source_dataset_id": 92, "dataset_name": "Products", "purpose": "DEPENDENCY"}
    ])
    assert service.snapshot(4, {"owner_key": "owner:4"})["models"] == []


def test_denied_scope_prevents_any_metadata_queries(monkeypatch):
    calls = setup(monkeypatch)

    def forbidden(conn, ws, user):
        raise HTTPException(403, "Workspace access denied")

    monkeypatch.setattr(service, "owned_workspace", forbidden)
    with pytest.raises(HTTPException) as exc:
        service.snapshot(4, {"owner_key": "other"})
    assert exc.value.status_code == 403
    assert calls == ["enabled", "tx"]


def test_disabled_feature_does_not_open_database(monkeypatch):
    def disabled():
        raise RuntimeError("Warehouse metadata is disabled")

    monkeypatch.setattr(service, "require_enabled", disabled)
    monkeypatch.setattr(service, "repository_transaction", lambda: pytest.fail("Database accessed"))
    with pytest.raises(RuntimeError, match="disabled"):
        service.snapshot(4, {"owner_key": "owner:4"})


def test_api_route_is_read_only():
    from app.main import app
    path = "/workspaces/{workspace_id}/warehouse/catalog"
    methods = app.openapi()["paths"][path]
    assert set(methods) == {"get"}
