"""414C3c: check revocation metadata without claiming or provider calls."""
from pathlib import Path

import pytest

from app.db.warehouse_query_publication_inspection_repository import (
    inspect_linked_publications,
)
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_reliability import reliability_pg, registered, intent, execute
from tests.test_warehouse_query_reservation_publication_links_postgres import (
    linked_pg, setup_attempt, insert_link,
)
from tests.test_warehouse_query_publication_retirement_postgres import (
    retirement_pg, advance, fence,
)


def inspect(f,attempt):
    with f.pg["connect"]() as conn:
        return inspect_linked_publications(
            conn,workspace_id=f.pg["workspace"],
            owner_key=f.pg["owner"]["owner_key"],
            attempt_id=attempt.attempt_id,
        )


def test_active_link_is_metadata_consistent_not_execution_permit(retirement_pg):
    f,pub=retirement_pg
    _,attempt,reservation=setup_attempt(f,pub)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    result=inspect(f,attempt)
    assert result.metadata_consistent
    assert not result.execution_authorized
    assert result.reason=="METADATA_CONSISTENT_ONLY"
    assert result.publication_ids==(pub["publication_id"],)


def test_missing_link_fails_closed(retirement_pg):
    f,pub=retirement_pg
    _,attempt,_=setup_attempt(f,pub)
    result=inspect(f,attempt)
    assert not result.metadata_consistent
    assert result.reason=="LINK_MISSING"


def test_released_reservation_fails_closed(retirement_pg):
    f,pub=retirement_pg
    svc,attempt,reservation=setup_attempt(f,pub)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    svc.release(reservation)
    result=inspect(f,attempt)
    assert not result.metadata_consistent
    assert result.reason=="RESERVATION_NOT_LIVE"


def test_owned_historical_link_still_consistent_after_head_advance(retirement_pg):
    f,pub=retirement_pg
    _,attempt,reservation=setup_attempt(f,pub)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    advance(f,"historical-still-linked")
    result=inspect(f,attempt)
    assert result.metadata_consistent
    assert result.execution_authorized is False


def test_foreign_owner_fails_before_inspection(retirement_pg):
    f,pub=retirement_pg
    _,attempt,reservation=setup_attempt(f,pub)
    with f.pg["connect"]() as conn:
        insert_link(conn,attempt,reservation)
    with pytest.raises(ValueError,match="WORKSPACE_NOT_OWNED"):
        with f.pg["connect"]() as conn:
            inspect_linked_publications(
                conn,workspace_id=f.pg["workspace"],
                owner_key=f.pg["other"]["owner_key"],
                attempt_id=attempt.attempt_id,
            )


def test_unlinked_historical_publication_can_be_retired_but_never_authorizes_query(retirement_pg):
    f,pub=retirement_pg
    _,attempt,_=setup_attempt(f,pub)
    advance(f,"historical-for-retirement")
    with f.pg["connect"]() as conn:
        fence(conn,f.resource,pub["publication_id"])
    result=inspect(f,attempt)
    assert result.reason=="LINK_MISSING"
    assert not result.execution_authorized


def test_inspection_has_no_provider_or_claim_invocation():
    sql=(Path(__file__).resolve().parents[1]/
         "app/db/warehouse_query_publication_inspection_repository.py"
         ).read_text(encoding="utf-8")
    assert "execution_authorized: bool = False" in sql
    assert "provider.execute(" not in sql
    assert "governance.claim(" not in sql
