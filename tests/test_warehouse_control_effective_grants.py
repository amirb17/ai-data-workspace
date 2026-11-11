"""Reject over-privileged query-control sessions before authenticated use."""
import pytest

from app.db.warehouse_canonical_query_connection import verify_control_write_boundary


class _One:
    def __init__(self, record): self.record = record
    def fetchone(self): return self.record


class _Conn:
    def __init__(self, *, overgrant=None, undergrant=None):
        self.overgrant=overgrant
        self.undergrant=undergrant
        self.calls=[]
    def execute(self, query, params=()):
        assert "has_table_privilege" in query
        assert len(params)==4 and len(set(params))==1
        name=params[0]
        self.calls.append(name)
        expected={
            "public.query_governance_reservations":(True,True,False,False),
            "datarise_warehouse_dev.warehouse_query_attempt_registry":(True,False,False,False),
            "datarise_warehouse_dev.warehouse_query_attempt_logical_drafts":(True,False,False,False),
            "datarise_warehouse_dev.warehouse_query_attempt_publication_pins":(True,False,False,False),
            "datarise_warehouse_dev.warehouse_query_attempt_reservation_links":(True,False,False,False),
            "datarise_warehouse_dev.warehouse_query_api_requests":(True,False,False,False),
            "datarise_warehouse_dev.warehouse_query_result_contracts":(True,False,False,False),
            "datarise_warehouse_dev.warehouse_query_dispatch_checkpoints":(True,False,False,False),
            "datarise_warehouse_dev.warehouse_query_send_intents":(True,False,False,False),
            "datarise_warehouse_dev.warehouse_query_provider_policies":(True,False,False,False),
            "datarise_warehouse_dev.warehouse_query_request_envelopes":(True,False,False,False),
        }.get(name,(False,False,False,False))
        if self.overgrant==name:
            expected=(expected[0],True,expected[2],expected[3])
        if self.undergrant==name:
            expected=(False,False,False,False)
        return _One(expected)


def test_control_acl_exact_grants_admitted():
    conn=_Conn()
    verify_control_write_boundary(conn)
    assert len(conn.calls)==19
    assert "public.users" in conn.calls
    assert "datarise_warehouse_dev.warehouse_query_send_intents" in conn.calls


def test_provider_worker_write_overgrant_is_rejected():
    with pytest.raises(RuntimeError,match="GRANT_DRIFT"):
        verify_control_write_boundary(_Conn(
            overgrant="datarise_warehouse_dev.warehouse_query_send_journal"))


def test_missing_admission_insert_is_rejected():
    with pytest.raises(RuntimeError,match="GRANT_DRIFT"):
        verify_control_write_boundary(_Conn(
            undergrant="datarise_warehouse_dev.warehouse_query_attempt_registry"))
