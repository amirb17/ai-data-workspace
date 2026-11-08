
import pytest

from app.services.sydney_warehouse_resource_service import (
    resolve_registered_sydney_resource,
)
from app.warehouse.sydney_binding import Approval, Registry
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_provider_postgres import provider_pg

def test_existing_development_resource_cannot_resolve_as_sydney(provider_pg, monkeypatch):
    fixture = provider_pg

    monkeypatch.setenv("WAREHOUSE_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "production")

    workspace = fixture.pg["workspace"]

    approved = Approval(
        "synthetic-project",
        workspace,
        "synthetic-loader@synthetic-project.iam.gserviceaccount.com",
        "synthetic-query@synthetic-project.iam.gserviceaccount.com",
        1,
    )

    with pytest.raises((PermissionError, ValueError)):
        resolve_registered_sydney_resource(
            workspace,
            fixture.model,
            fixture.version,
            fixture.resource,
            fixture.pg["owner"],
            registry=Registry([approved]),
            expected_approval=approved.fingerprint,
            publication_id=1,
            revision=1,
            schema_sha256="a" * 64,
            source_fingerprint="b" * 64,
        )
