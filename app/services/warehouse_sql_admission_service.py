"""Step 413B: admission-only warehouse SQL reservations via existing governance.

Caller supplies a trusted server-side 413A dry run and an authenticated
QueryGovernanceService. Durable admission remains exclusively owned by the
existing PostgreSQL governance repository, including locks, budgets,
ownership, concurrency and token conflict checks.

Neither an ACTIVE reservation nor this result permits warehouse execution.
This module NEVER claims a reservation, dispatches SQL or calls BigQuery.
"""
from dataclasses import dataclass
from collections.abc import Mapping

from app.query_governance.providers.base import CompiledQuery
from app.query_governance.types import Decision, EstimateConfidence, QueryEstimate
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_dry_run_service import WarehouseDryRunEstimate


@dataclass(frozen=True)
class WarehouseAdmission:
    query: CompiledQuery
    decision: Decision | None
    reservation_id: int | None
    reason: str
    reserved_bytes: int
    reused: bool = False
    execution_authorized: bool = False


def admit_warehouse_query_attempt(
    *, dry_run: WarehouseDryRunEstimate,
    governance: QueryGovernanceService,
    requested_rows: int,
    lease_seconds: int = 120,
) -> WarehouseAdmission:
    """Reserve a single SQL attempt; never automatically claim or run it.

    This boundary does not recheck publication metadata or validate the
    origin of the dry-run proof: the eventual dispatch gateway MUST do both
    using fresh authenticated sources and server-side compilation.
    """
    if (type(dry_run) is not WarehouseDryRunEstimate
            or type(dry_run.query) is not CompiledQuery
            or dry_run.query.compiler_version != 2
            or type(dry_run.estimate) is not QueryEstimate
            or dry_run.estimate.confidence not in (
                EstimateConfidence.EXACT,EstimateConfidence.UPPER_BOUND)
            or type(dry_run.estimate.bytes_processed) is not int
            or dry_run.estimate.bytes_processed < 1
            or not dry_run.metadata_and_publication_checked
            or dry_run.admission_created or dry_run.execution_authorized):
        raise ValueError("Trusted non-executing warehouse estimate required")
    if type(governance) is not QueryGovernanceService:
        raise ValueError("Authenticated durable governance service required")
    if dry_run.query.workspace_id != governance.workspace_id:
        raise ValueError("Warehouse query and governance workspace differ")
    if (type(requested_rows) is not int or not 1 <= requested_rows <= 500
            or type(lease_seconds) is not int or not 1 <= lease_seconds <= 900):
        raise ValueError("Bounded warehouse output rows and lease required")

    response=governance.admit(
        consumer="SQL",provider="BIGQUERY",
        request_token=dry_run.query.request_token,
        intent_sha256=dry_run.query.intent_sha256,
        estimate=dry_run.estimate,
        requested_rows=requested_rows,lease_seconds=lease_seconds,
    )
    if not isinstance(response,Mapping):
        raise ValueError("Governance admission result required")
    reservation=response.get("reservation")
    reused=response.get("reused")
    if type(reused) is not bool:
        raise ValueError("Explicit reservation reuse status required")
    if reservation is None:
        outcome=response.get("decision")
        if (outcome is None or outcome.decision not in (
            Decision.BLOCKED,Decision.REVIEW_REQUIRED)
                or reused):
            raise ValueError("Explicit blocked or review-required decision needed")
        return WarehouseAdmission(
            dry_run.query,outcome.decision,None,outcome.reason,0,
        )
    if not isinstance(reservation,Mapping):
        raise ValueError("Malformed durable reservation")
    # An existing attempt is allowed to be inspected, not executed. The
    # repository rejects same-token changed intents/estimates atomically.
    if (reservation.get("status") != "ACTIVE"
            or reservation.get("workspace_id") != governance.workspace_id
            or reservation.get("owner_key") != governance.owner_key
            or reservation.get("consumer") != "SQL"
            or reservation.get("provider") != "BIGQUERY"
            or reservation.get("request_token") != dry_run.query.request_token
            or reservation.get("intent_sha256") != dry_run.query.intent_sha256
            or reservation.get("estimated_bytes") != dry_run.estimate.bytes_processed
            or reservation.get("estimate_confidence") != dry_run.estimate.confidence.value
            or reservation.get("output_rows") != requested_rows
            or type(reservation.get("reservation_id")) is not int
            or reservation["reservation_id"] < 1
            or type(reservation.get("reserved_bytes")) is not int
            or reservation["reserved_bytes"] < dry_run.estimate.bytes_processed
            or type(reservation.get("maximum_bytes_billed")) is not int
            or reservation["reserved_bytes"] > reservation["maximum_bytes_billed"]):
        raise ValueError("Incomplete or inconsistent ACTIVE warehouse reservation")
    return WarehouseAdmission(
        dry_run.query,Decision.ALLOWED,reservation["reservation_id"],
        "ACTIVE_RESERVATION_REUSED" if reused else "ACTIVE_RESERVATION_CREATED",
        reservation["reserved_bytes"],reused=reused,
    )
