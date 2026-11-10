"""414D1: internal readiness never claims, admits, or dispatches."""
import ast
from pathlib import Path

import pytest

from app.services.warehouse_sql_gateway_readiness_service import (
    inspect_gateway_readiness_without_dispatch,
)
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_reliability import reliability_pg
from tests.test_warehouse_query_reservation_publication_links_postgres import (
    linked_pg,setup_attempt,insert_link,
)
from tests.test_warehouse_query_publication_retirement_postgres import (
    retirement_pg,
)
from tests.test_warehouse_sql_admission_postgres import service


def test_linked_active_attempt_is_metadata_only(retirement_pg):
    f,publication=retirement_pg
    governance,attempt,reservation=setup_attempt(f,publication)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    inspected=inspect_gateway_readiness_without_dispatch(
        governance=governance,attempt_handle=attempt.attempt_handle,
    )
    assert inspected.metadata_consistent
    assert inspected.reservation_id==reservation
    assert inspected.state=="METADATA_CONSISTENT_ONLY"
    assert not inspected.execution_authorized
    assert not inspected.provider_dispatched
    assert not inspected.reservation_claimed
    assert governance.reservation(reservation)["status"]=="ACTIVE"


def test_unlinked_attempt_denied_without_claim(retirement_pg):
    f,publication=retirement_pg
    governance,attempt,reservation=setup_attempt(f,publication)
    inspected=inspect_gateway_readiness_without_dispatch(
        governance=governance,attempt_handle=attempt.attempt_handle,
    )
    assert not inspected.metadata_consistent
    assert inspected.state=="LINK_MISSING"
    assert governance.reservation(reservation)["status"]=="ACTIVE"


def test_terminal_attempt_denied_without_re_admission(retirement_pg):
    f,publication=retirement_pg
    governance,attempt,reservation=setup_attempt(f,publication)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    governance.release(reservation)
    inspected=inspect_gateway_readiness_without_dispatch(
        governance=governance,attempt_handle=attempt.attempt_handle,
    )
    assert not inspected.metadata_consistent
    assert inspected.state=="RESERVATION_NOT_LIVE"


def test_foreign_governance_context_rejected(retirement_pg):
    f,publication=retirement_pg
    _,attempt,_=setup_attempt(f,publication)
    from app.db.query_governance_repository import ReservationConflict
    with pytest.raises(ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        inspect_gateway_readiness_without_dispatch(
            governance=service(f.pg,owner=f.pg["other"]),
            attempt_handle=attempt.attempt_handle,
        )


def test_no_query_execution_surface_added():
    source=(Path(__file__).resolve().parents[1]/
            "app/services/warehouse_sql_gateway_readiness_service.py")
    tree=ast.parse(source.read_text(encoding="utf-8"))
    attributes={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
    assert not attributes.intersection({
        "execute","claim","admit","estimate","recover","complete","fail",
    })
    assert "execution_authorized: bool = False" in source.read_text()
    assert "provider_dispatched: bool = False" in source.read_text()
    assert "reservation_claimed: bool = False" in source.read_text()
