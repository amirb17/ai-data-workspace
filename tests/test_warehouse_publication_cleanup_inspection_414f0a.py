"""414F0-A: metadata cleanup inspection never authorizes BigQuery deletion."""
from dataclasses import asdict
from pathlib import Path

import pytest

from app.services.warehouse_publication_cleanup_inspection_service import (
    inspect_publication_cleanup_without_deletion as inspect,
)
from tests.test_warehouse_sql_dispatch_checkpoint_414d5 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, check, change, scalar,
)
from tests.test_warehouse_query_reservation_publication_links_postgres import (
    linked_pg,
)
from tests.test_warehouse_reliability import registered, intent, execute

ROOT=Path(__file__).resolve().parents[1]


def test_current_head_never_admits_cloud_cleanup(served):
    s=served
    result=inspect(resource_id=s.f.resource,publication_id=s.pub["publication_id"])
    assert result.current_head
    assert result.query_dependency
    assert not result.retired
    assert result.reason=="CURRENT_PUBLICATION_HEAD"
    assert not result.physical_deletion_authorized
    assert not result.provider_dispatched


def test_permanent_checkpoint_hold_survives_publication_advance(served):
    s=served
    check(s)
    change(s,"publication")
    result=inspect(resource_id=s.f.resource,publication_id=s.pub["publication_id"])
    assert not result.current_head
    assert result.query_dependency
    assert not result.retired
    assert result.reason=="QUERY_RETENTION_DEPENDENCY"
    assert not result.physical_deletion_authorized
    # Historical retirement remains blocked even after the head changes.
    import psycopg
    with pytest.raises(psycopg.DatabaseError,match="dependency"):
        with s.f.pg["connect"]() as conn:
            conn.execute(
                """INSERT INTO warehouse_query_publication_retirement_fences
                   (resource_id,publication_id) VALUES (%s,%s)""",
                (s.f.resource,s.pub["publication_id"]),
            )


def test_unheld_historical_publication_needs_retirement_fence(linked_pg):
    f,first=linked_pg
    with f.pg["connect"]() as conn:
        conn.execute(
            (ROOT/"migrations/0051_warehouse_query_publication_retirement_fences.sql")
            .read_text(encoding="utf-8")
        )
    manifests=registered(f)
    second=execute(f,intent(f,manifests,kind="INCREMENTAL",key="cleanup-audit-head"),manifests)
    assert second["publication_id"]!=first["publication_id"]
    before=inspect(resource_id=f.resource,publication_id=first["publication_id"])
    assert before.reason=="RETIREMENT_FENCE_MISSING"
    assert not before.query_dependency
    assert not before.physical_deletion_authorized
    with f.pg["connect"]() as conn:
        conn.execute(
            """INSERT INTO warehouse_query_publication_retirement_fences
               (resource_id,publication_id) VALUES (%s,%s)""",
            (f.resource,first["publication_id"]),
        )
    after=inspect(resource_id=f.resource,publication_id=first["publication_id"])
    assert after.retired
    assert after.reason=="RETIRED_METADATA_ONLY_CLOUD_ENFORCEMENT_UNVERIFIED"
    assert not after.physical_deletion_authorized


@pytest.mark.parametrize("kwargs",[
    {"resource_id":0,"publication_id":1},
    {"resource_id":1,"publication_id":-1},
    {"resource_id":True,"publication_id":1},
    {"resource_id":1,"publication_id":"1"},
])
def test_invalid_identifiers_denied(kwargs):
    with pytest.raises(ValueError,match="Positive publication identity"):
        inspect(**kwargs)


def test_nonexistent_publication_denied(linked_pg):
    f,_=linked_pg
    with f.pg["connect"]() as conn:
        conn.execute(
            (ROOT/"migrations/0051_warehouse_query_publication_retirement_fences.sql")
            .read_text(encoding="utf-8")
        )
    with pytest.raises(ValueError,match="WAREHOUSE_PUBLICATION_NOT_FOUND"):
        inspect(resource_id=f.resource,publication_id=987654321)


def test_inspection_has_no_cloud_or_deletion_contract():
    source=(ROOT/"app/services/warehouse_publication_cleanup_inspection_service.py").read_text(
        encoding="utf-8",
    )
    assert "physical_deletion_authorized: bool = False" in source
    assert "provider_dispatched: bool = False" in source
    for forbidden in ("delete_table(", "client.query(", "provider.execute(", "governance.claim("):
        assert forbidden not in source
