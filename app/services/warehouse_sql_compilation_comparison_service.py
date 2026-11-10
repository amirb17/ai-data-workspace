"""414D3: non-executing, DB-grounded compiled query / publication comparison.

This verifies consistency, not compilation provenance. The caller-supplied
CompiledQuery and InternalDraftBinding are forgeable Python values, so even
a positive result cannot authorize a provider job. An authenticated server
resolver and reviewed compiler are still mandatory for dispatch.
"""
from dataclasses import dataclass

from psycopg.rows import dict_row

from app.db.database import repository_transaction
from app.db import warehouse_query_attempt_repository as attempts
from app.db.warehouse_query_publication_inspection_repository import (
    inspect_linked_publications,
)
from app.query_governance.providers.base import CompiledQuery
from app.services.query_governance_service import QueryGovernanceService
from app.warehouse.sql_physical_binding import InternalDraftBinding
from app.services.warehouse_sql_claim_rehearsal_service import (
    _physical_pins_consistent,
)


@dataclass(frozen=True)
class WarehouseCompilationComparison:
    reservation_id: int
    consistent: bool
    reason: str
    execution_authorized: bool = False
    reservation_claimed: bool = False
    provider_dispatched: bool = False


def compare_compilation_to_registered_publications(
    *, governance: QueryGovernanceService, attempt_handle: str,
    query: CompiledQuery, binding: InternalDraftBinding,
) -> WarehouseCompilationComparison:
    """Read and compare authoritative records; never claim or dispatch."""
    if (type(governance) is not QueryGovernanceService
            or type(query) is not CompiledQuery
            or type(binding) is not InternalDraftBinding
            or binding.executable is not False
            or query.compiler_version != 2
            or query.workspace_id != governance.workspace_id
            or not _physical_pins_consistent(binding,governance.workspace_id)):
        raise ValueError("Valid non-executable compiled publication evidence required")

    with repository_transaction() as conn:
        original=attempts.read_attempt(
            conn,workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,attempt_handle=attempt_handle,
        )
        inspection=inspect_linked_publications(
            conn,workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,attempt_id=original["attempt_id"],
        )
        def deny(code):
            return WarehouseCompilationComparison(
                inspection.reservation_id,False,code,
            )
        if not inspection.metadata_consistent:
            return deny(inspection.reason)

        reservation=conn.cursor(row_factory=dict_row).execute(
            """SELECT request_token,intent_sha256,status
               FROM query_governance_reservations WHERE reservation_id=%s""",
            (inspection.reservation_id,),
        ).fetchone()
        if (reservation is None
                or reservation["request_token"]!=query.request_token
                or reservation["intent_sha256"]!=query.intent_sha256
                or reservation["status"] not in ("ACTIVE","EXECUTING")):
            return deny("COMPILED_RESERVATION_MISMATCH")
        if (query.request_token!=original["request_token"].strip()
                or len(binding.models)!=len(query.physical_tables)
                or query.physical_tables!=tuple(
                    model.physical_table for model in binding.models
                )):
            return deny("COMPILED_PHYSICAL_SCOPE_MISMATCH")

        pins=conn.cursor(row_factory=dict_row).execute(
            """SELECT p.resource_id,p.publication_id,p.publication_revision,
                      r.warehouse_model_id,r.warehouse_model_version_id,
                      r.generation
               FROM warehouse_query_attempt_publication_pins p
               JOIN warehouse_resources r ON r.resource_id=p.resource_id
               WHERE p.attempt_id=%s ORDER BY p.resource_id""",
            (original["attempt_id"],),
        ).fetchall()
        pinned={
            (
                p["resource_id"],p["warehouse_model_id"],
                p["warehouse_model_version_id"],p["generation"],
                p["publication_id"],p["publication_revision"],
            ) for p in pins
        }
        compiled={
            (
                m.resource_id,m.model_id,m.model_version_id,m.generation,
                m.publication_id,m.publication_revision,
            ) for m in binding.models
        }
        if len(pinned)!=len(binding.models) or compiled!=pinned:
            return deny("COMPILED_PUBLICATION_PINS_MISMATCH")
        return WarehouseCompilationComparison(
            inspection.reservation_id,True,"CONSISTENT_UNTRUSTED_COMPILATION",
        )
