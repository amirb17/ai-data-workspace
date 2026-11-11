"""414F0-B actual PostgreSQL multi-role inventory in disposable schema."""
import pytest
from app.warehouse.pg_runtime_role_inventory import inspect_runtime_role_inventory
from app.warehouse.pg_result_reader_permission_probe import PROTECTED_TABLES
from tests.test_warehouse_query_envelope_pinning_414f1 import (
    pg,modeling_pg,fact_provider_pg,genuinely_served_query,
    served,checkpointed,prepared,lookup,unbound,pinned_db,
)

ROLES=(
    "datarise_warehouse_result_reader",
    "datarise_warehouse_query_worker",
    "datarise_warehouse_publisher",
    "datarise_warehouse_cleanup",
    "datarise_warehouse_api",
)

def inspect(conn,roles=ROLES):
    return inspect_runtime_role_inventory(
        conn,role_names=roles,protected_tables=PROTECTED_TABLES,
    )


def test_actual_database_reports_each_candidate_without_granting_roles(pinned_db):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        outcome=inspect(conn)
        current=conn.execute("SELECT current_user").fetchone()[0]
    assert outcome.classification=="NO_GO"
    assert not outcome.deployment_authorized
    assert not outcome.live_restricted_logins_verified
    assert outcome.current_user==current
    assert tuple(r.role for r in outcome.roles)==ROLES
    assert all(r.findings for r in outcome.roles)
    assert not client.calls


def test_missing_role_is_reported_not_silently_treated_as_denied(pinned_db):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        outcome=inspect(conn,("datarise_absent_414f0b_never_created",))
    record=outcome.roles[0]
    assert not record.exists
    assert "ROLE_MISSING" in record.findings
    assert record.missing_tables==PROTECTED_TABLES
    assert outcome.classification=="NO_GO"
    assert not client.calls


@pytest.mark.parametrize("roles",[
    (),
    ("postgres",),
    ("datarise_a",),
    ("datarise_query_reader",)*2,
    ("datarise_bad;DROP",),
    tuple("datarise_role_"+str(i) for i in range(11)),
])
def test_invalid_or_duplicate_deployment_role_matrix_rejected(pinned_db,roles):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        with pytest.raises(ValueError,match="REVIEWED_RUNTIME_ROLE_AND_TABLE_MATRIX_REQUIRED"):
            inspect(conn,roles)
    assert not client.calls


def test_invalid_protected_table_matrix_rejected(pinned_db):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        with pytest.raises(ValueError):
            inspect_runtime_role_inventory(conn,role_names=ROLES,protected_tables=("users;DROP",))
    assert not client.calls


def test_inspection_is_catalog_read_only(pinned_db):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        before=conn.execute("SELECT current_user, COUNT(*) FROM pg_roles").fetchone()
        result=inspect(conn)
        after=conn.execute("SELECT current_user, COUNT(*) FROM pg_roles").fetchone()
    assert before==after
    assert len(result.roles)==5
    assert not result.deployment_authorized
    assert not client.calls
