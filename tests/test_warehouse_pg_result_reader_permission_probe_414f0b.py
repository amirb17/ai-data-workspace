"""414F0-B actual-connection role inspection, isolated PostgreSQL schema."""
import pytest

from app.warehouse.pg_result_reader_permission_probe import (
    PROTECTED_TABLES,probe_warehouse_result_reader,
)
from tests.test_warehouse_query_envelope_pinning_414f1 import (
    pg,modeling_pg,fact_provider_pg,genuinely_served_query,
    served,checkpointed,prepared,lookup,unbound,pinned_db,
)


def test_readonly_probe_of_actual_connected_postgres_is_never_approval(pinned_db):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        evidence=probe_warehouse_result_reader(
            conn,expected_role="datarise_warehouse_result_reader",
        )
        assert evidence.schema==conn.execute("SELECT current_schema()").fetchone()[0]
        assert evidence.session_user==conn.execute("SELECT session_user").fetchone()[0]
        assert evidence.current_user==conn.execute("SELECT current_user").fetchone()[0]
    assert evidence.classification=="NO_GO"
    assert not evidence.approval_authorized
    assert len(evidence.protected)+len(evidence.missing_protected_tables)==len(PROTECTED_TABLES)
    assert evidence.findings
    assert not client.calls


def test_privileged_dev_connection_cannot_masquerade_as_restricted_login(pinned_db):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        current=conn.execute("SELECT current_user").fetchone()[0]
        evidence=probe_warehouse_result_reader(
            conn,expected_role="datarise_warehouse_result_reader",
        )
    if current!="datarise_warehouse_result_reader":
        assert "DIRECT_RESTRICTED_ROLE_LOGIN_REQUIRED" in evidence.findings
    if evidence.superuser:
        assert "PRIVILEGED_OR_UNVERIFIED_RUNTIME_SESSION" in evidence.findings
    assert evidence.classification=="NO_GO"
    assert not evidence.approval_authorized


@pytest.mark.parametrize("role",[
    "postgres","datarise_","datarise_x;DROP TABLE users",
    "DATARISE_WAREHOUSE","datarise_a","",None,
])
def test_untrusted_expected_role_rejected_before_catalog_queries(pinned_db,role):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        with pytest.raises(ValueError,match="TRUSTED_EXPECTED_RUNTIME_ROLE_REQUIRED"):
            probe_warehouse_result_reader(conn,expected_role=role)
    assert not client.calls


def test_protected_metadata_privileges_are_reported_not_modified(pinned_db):
    s,lease,client=pinned_db
    with s.f.pg["connect"]() as conn:
        before=conn.execute(
            "SELECT current_user, COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema()"
        ).fetchone()
        result=probe_warehouse_result_reader(
            conn,expected_role="datarise_warehouse_result_reader",
        )
        after=conn.execute(
            "SELECT current_user, COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema()"
        ).fetchone()
    assert before==after
    assert all(set(record.mutation_permissions) <= {
        "INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER"
    } for record in result.protected)
    assert not result.approval_authorized
