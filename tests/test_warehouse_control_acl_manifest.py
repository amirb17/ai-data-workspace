"""Offline control ACL manifest regression tests.

Tests intentionally do not connect to PostgreSQL or create credentials.
"""
from tools.warehouse_control_acl import (
    PUBLIC_SELECT, PUBLIC_WRITES, QUERY_SELECT, QUERY_WRITES,
    CONTROL, SCHEMA,
)


def test_control_role_has_bounded_canonical_reads():
    assert CONTROL == "datarise_wh_control"
    assert SCHEMA == "datarise_warehouse_dev"
    assert len(PUBLIC_SELECT) == len(set(PUBLIC_SELECT))
    assert "users" in PUBLIC_SELECT and "auth_sessions" in PUBLIC_SELECT
    assert set(PUBLIC_WRITES) == {"query_governance_reservations"}
    assert PUBLIC_WRITES["query_governance_reservations"] == ("INSERT", "UPDATE")
    assert "users" not in PUBLIC_WRITES
    assert "auth_sessions" not in PUBLIC_WRITES
    assert "warehouse_publications" not in PUBLIC_WRITES


def test_control_role_has_no_provider_or_accounting_write_privileges():
    assert len(QUERY_SELECT) == 17
    assert len(QUERY_SELECT) == len(set(QUERY_SELECT))
    assert all(name.startswith("warehouse_query_") for name in QUERY_SELECT)
    assert set(QUERY_WRITES).issubset(QUERY_SELECT)
    assert all(rights == ("INSERT",) for rights in QUERY_WRITES.values())
    assert {"warehouse_query_send_intents", "warehouse_query_provider_policies", "warehouse_query_request_envelopes"} <= set(QUERY_WRITES)
    for forbidden in (
        "warehouse_query_send_journal",
        "warehouse_query_recovery_attestations",
        "warehouse_query_result_attestations",
        "warehouse_query_dispatch_revocations",
    ):
        assert forbidden not in QUERY_WRITES


def test_control_role_can_only_insert_immutable_query_admission_records():
    assert set(QUERY_WRITES) == {
        "warehouse_query_attempt_registry",
        "warehouse_query_attempt_logical_drafts",
        "warehouse_query_attempt_publication_pins",
        "warehouse_query_attempt_reservation_links",
        "warehouse_query_api_requests",
        "warehouse_query_result_contracts",
        "warehouse_query_dispatch_checkpoints",
        "warehouse_query_send_intents",
        "warehouse_query_provider_policies",
        "warehouse_query_request_envelopes",
    }
