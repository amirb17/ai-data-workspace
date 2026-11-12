"""Accounting retains the governance settlement lock, not metadata UPDATE grants."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import warehouse_sql_attested_accounting_service as accounting
from app.services import warehouse_sql_terminal_evidence_service as evidence
from app.db import warehouse_query_send_repository as repo
from tools.warehouse_worker_acl import READS, WRITES, SCHEMA


class StopAtIdentity(Exception):
    pass


@contextmanager
def _transaction(*args, **kwargs):
    yield object()


def test_accounting_inspects_immutable_attempt_without_row_update_lock():
    with patch.object(repo,"transaction",_transaction), patch.object(
            accounting,"_inspect_terminal_evidence_locked",
            return_value=SimpleNamespace(observation="SUCCEEDED")
    ), patch.object(repo,"checkpoint",return_value={
        "attempt_id":42,
    }), patch.object(repo,"lock_identity",side_effect=StopAtIdentity
    ) as identity:
        with pytest.raises(StopAtIdentity):
            accounting.settle_verified_warehouse_terminal(attempt_id=42)
    assert identity.call_args.kwargs == {"lock":False}


def test_terminal_evidence_uses_read_only_metadata_locks():
    with patch.object(repo,"checkpoint",return_value={
        "attempt_id":42,
    }),patch.object(repo,"lock_identity",side_effect=StopAtIdentity
    ) as identity:
        with pytest.raises(StopAtIdentity):
            evidence._inspect_terminal_evidence_locked(object(),42)
    assert identity.call_args.kwargs == {"lock":False}


def test_accounting_grants_only_read_access_to_ownership_and_envelope():
    assert ("public","workspaces") in READS["accounting"]
    assert (SCHEMA,"warehouse_query_request_envelopes") in READS["accounting"]
    assert ("public","workspaces") not in WRITES["accounting"]
    assert (SCHEMA,"warehouse_query_request_envelopes") not in WRITES["accounting"]
    assert WRITES["accounting"] == {
        ("public","query_governance_reservations"):("UPDATE",),
        ("public","query_governance_usage"):("INSERT",),
    }
