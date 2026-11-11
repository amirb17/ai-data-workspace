"""Offline invariants for independent, least-privilege warehouse worker roles."""
from tools.warehouse_worker_acl import READS, WRITES, ROLES, SCHEMA


def test_worker_role_identities_are_distinct_and_not_control():
    assert len(ROLES) == 4
    assert len(set(ROLES.values())) == len(ROLES)
    assert "datarise_wh_control" not in ROLES.values()
    assert set(ROLES) == {
        "provider_submission", "recovery_attestation",
        "accounting", "result_authorization",
    }


def test_no_worker_can_modify_application_identity_or_publications():
    forbidden={
        ("public", "users"), ("public", "auth_sessions"),
        ("public", "workspaces"), ("public", "warehouse_publications"),
        ("public", "warehouse_resources"),
    }
    for duty,writes in WRITES.items():
        assert not forbidden.intersection(writes), duty
        for table,rights in writes.items():
            assert rights
            assert set(rights).issubset({"INSERT","UPDATE"})
            assert table[0] in ("public", SCHEMA)


def test_production_workers_never_get_ddl_delete_or_truncate():
    for duty,writes in WRITES.items():
        assert "DELETE" not in {r for rights in writes.values() for r in rights}
        assert "TRUNCATE" not in {r for rights in writes.values() for r in rights}
    assert WRITES["result_authorization"] == {}


def test_attestation_and_submission_writes_cannot_modify_accounting_ledger():
    assert ("public", "query_governance_usage") not in WRITES["provider_submission"]
    assert ("public", "query_governance_usage") not in WRITES["recovery_attestation"]
    assert ("public", "query_governance_usage") in WRITES["accounting"]
    assert ("public", "query_governance_reservations") in READS["accounting"]
    assert ("public", "auth_sessions") in READS["result_authorization"]


def test_worker_protocol_table_set_has_no_unknown_schema():
    for reads in READS.values():
        assert all(schema in ("public", SCHEMA) for schema, _ in reads)
