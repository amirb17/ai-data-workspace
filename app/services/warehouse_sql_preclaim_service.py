"""Step 413C: pre-claim warehouse reservation revalidation and safe release.

Read-only checks and BigQuery cost dry-runs happen ONLY while the durable
reservation is ACTIVE. This function never claims, executes or recovers.
Callbacks must be server-authenticated fresh SQL/publication verifiers; they
cannot authorize execution and must be re-run by any future dispatch gateway.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.query_governance.providers.base import CompiledQuery
from app.query_governance.types import EstimateConfidence, QueryEstimate
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_admission_service import WarehouseAdmission
from app.services.warehouse_sql_compiled_evidence_service import WarehouseCompiledEvidence


class PreclaimState(StrEnum):
    REVALIDATED_ACTIVE = "REVALIDATED_ACTIVE"
    RELEASED_REVALIDATION_REQUIRED = "RELEASED_REVALIDATION_REQUIRED"


@dataclass(frozen=True)
class WarehousePreclaimResult:
    state: PreclaimState
    reservation_id: int
    execution_authorized: bool = False
    reservation_claimed: bool = False


def _matches_current(
    attempt: CompiledQuery, current: CompiledQuery,
    evidence: WarehouseCompiledEvidence,
) -> bool:
    return (
        type(current) is CompiledQuery
        and type(evidence) is WarehouseCompiledEvidence
        and current.compiler_version == attempt.compiler_version == 2
        and current.workspace_id == attempt.workspace_id == evidence.workspace_id
        and current.intent_sha256 == attempt.intent_sha256
        and current.sql == attempt.sql
        and current.physical_tables == attempt.physical_tables
        and evidence.model_count == len(attempt.physical_tables)
        and evidence.table_metadata_verified
        and evidence.current_publication_checked_after_metadata
        and not evidence.query_execution_authorized
        and not evidence.row_contents_verified
    )


def _active_reservation(
    row: Mapping, admission: WarehouseAdmission, governance: QueryGovernanceService,
) -> bool:
    query=admission.query
    return (
        isinstance(row, Mapping)
        and row.get("status") == "ACTIVE"
        and row.get("reservation_id") == admission.reservation_id
        and row.get("workspace_id") == governance.workspace_id
        and row.get("owner_key") == governance.owner_key
        and row.get("provider") == "BIGQUERY"
        and row.get("consumer") == "SQL"
        and row.get("request_token") == query.request_token
        and row.get("intent_sha256") == query.intent_sha256
        and type(row.get("reserved_bytes")) is int
        and row["reserved_bytes"] >= 1
        and type(row.get("maximum_bytes_billed")) is int
        and row["reserved_bytes"] <= row["maximum_bytes_billed"]
        and type(row.get("output_rows")) is int
        and 1 <= row["output_rows"] <= 500
    )


def revalidate_active_warehouse_reservation(
    *, admission: WarehouseAdmission,
    governance: QueryGovernanceService,
    provider,
    verify_current: Callable[[], tuple[CompiledQuery, WarehouseCompiledEvidence]],
) -> WarehousePreclaimResult:
    """Recheck trusted template, dry run and ACTIVE row; never claim or dispatch.

    On any *validation* failure release only an ACTIVE reservation using the
    repository's release guard. If another worker has already claimed it,
    release raises rather than deleting execution/recovery accounting.
    The function cannot solve a race occurring after it returns; execution
    still requires independent, dispatch-time checks.
    """
    if (type(admission) is not WarehouseAdmission
            or type(admission.query) is not CompiledQuery
            or admission.reservation_id is None
            or type(admission.reservation_id) is not int
            or admission.reservation_id < 1
            or admission.execution_authorized
            or admission.decision != "ALLOWED"
            or type(governance) is not QueryGovernanceService
            or admission.query.workspace_id != governance.workspace_id
            or getattr(provider,"identity",None) != "BIGQUERY"
            or not callable(verify_current)):
        raise ValueError("Owned, ACTIVE candidate and trusted verifier required")

    # Never release an unknown/terminal/foreign reservation even when a check
    # fails. A claimed reservation must remain held for provider recovery.
    row=governance.reservation(admission.reservation_id)
    if not _active_reservation(row,admission,governance):
        raise ValueError("CURRENT_ACTIVE_WAREHOUSE_RESERVATION_REQUIRED")

    try:
        first=verify_current()
        if (type(first) is not tuple or len(first)!=2
                or not _matches_current(admission.query,*first)):
            raise ValueError("WAREHOUSE_PRECLAIM_PUBLICATION_CHANGED")

        fresh=provider.estimate(admission.query)
        if (type(fresh) is not QueryEstimate
                or fresh.confidence not in (
                    EstimateConfidence.EXACT,EstimateConfidence.UPPER_BOUND)
                or type(fresh.bytes_processed) is not int
                or fresh.bytes_processed < 1
                or fresh.bytes_processed > row["reserved_bytes"]
                or fresh.bytes_processed > row["maximum_bytes_billed"]):
            raise ValueError("WAREHOUSE_PRECLAIM_ESTIMATE_EXCEEDS_RESERVATION")

        second=verify_current()
        if (type(second) is not tuple or len(second)!=2
                or not _matches_current(admission.query,*second)
                or second[0] != first[0]):
            raise ValueError("WAREHOUSE_PRECLAIM_PUBLICATION_CHANGED")

        # Read one more time in a new owned transaction to detect changes to
        # status/identity/budget. This does not lock future dispatch.
        final=governance.reservation(admission.reservation_id)
        if (not _active_reservation(final,admission,governance)
                or final != row):
            raise ValueError("WAREHOUSE_PRECLAIM_RESERVATION_CHANGED")
    except Exception:
        # The existing repository releases ACTIVE only. Never swallow a
        # concurrent claim or an uncertain database/release outcome.
        governance.release(admission.reservation_id)
        return WarehousePreclaimResult(
            PreclaimState.RELEASED_REVALIDATION_REQUIRED,admission.reservation_id,
        )
    return WarehousePreclaimResult(
        PreclaimState.REVALIDATED_ACTIVE,admission.reservation_id,
    )
