"""Step 413D: reservation claim rehearsal, with provider execution hard-disabled.

This is an INTERNAL transition test tool, never an HTTP route or executor.
The caller MUST supply an authenticated fresh publication resolver and trusted
backend compilation. It inspects the exact immutable query before and after
claim; once claimed, ANY error leaves the reservation EXECUTING for recovery.
This is NOT atomic with a future provider dispatch and grants no permission
to execute a query. A production gateway still needs end-to-end authorization.
"""
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import re

from app.query_governance.providers.base import CompiledQuery
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_admission_service import WarehouseAdmission
from app.services.warehouse_sql_preclaim_service import (
    PreclaimState, WarehousePreclaimResult, _matches_current,
)
from app.services.warehouse_sql_compiled_evidence_service import WarehouseCompiledEvidence
from app.warehouse.sql_claimed_reservation_guard import (
    inspect_claimed_warehouse_reservation,
)
from app.warehouse.sql_physical_binding import InternalDraftBinding

_TABLE_PIN = re.compile(
    r"^[a-z][a-z0-9-]*[a-z0-9]\.dr_dev_w(?P<workspace>[1-9][0-9]*)"
    r"\.m(?P<model>[1-9][0-9]*)_v(?P<version>[1-9][0-9]*)"
    r"_g(?P<generation>[1-9][0-9]*)_p(?P<publication>[1-9][0-9]*)"
    r"_r(?P<revision>[1-9][0-9]*)$"
)


def _physical_pins_consistent(binding, workspace_id):
    if not binding.models or len(binding.models) > 2:
        return False
    for model in binding.models:
        name=model.physical_table
        match=_TABLE_PIN.fullmatch(name) if isinstance(name,str) else None
        if match is None or tuple(int(match.group(field)) for field in (
            "workspace","model","version","generation","publication","revision"
        )) != (
            workspace_id,model.model_id,model.model_version_id,
            model.generation,model.publication_id,model.publication_revision
        ):
            return False
    return True


class WarehouseClaimRehearsalState(StrEnum):
    CLAIMED_NO_EXECUTION = "CLAIMED_NO_EXECUTION"


class WarehouseClaimRecoveryRequired(RuntimeError):
    """The existing EXECUTING reservation must be recovered, never released."""

    def __init__(self,reservation_id:int):
        self.reservation_id=reservation_id
        super().__init__("WAREHOUSE_CLAIM_RECOVERY_REQUIRED")


@dataclass(frozen=True)
class WarehouseClaimRehearsal:
    reservation_id: int
    state: WarehouseClaimRehearsalState
    execution_authorized: bool = False
    provider_dispatched: bool = False


def inspect_claim_transition_without_dispatch(
    *,
    governance: QueryGovernanceService,
    admission: WarehouseAdmission,
    preclaim: WarehousePreclaimResult,
    resolve_current: Callable[[], tuple[InternalDraftBinding,CompiledQuery,WarehouseCompiledEvidence]],
) -> WarehouseClaimRehearsal:
    """Claim at most once but deliberately NEVER invoke provider execution."""
    if (type(governance) is not QueryGovernanceService
            or type(admission) is not WarehouseAdmission
            or type(preclaim) is not WarehousePreclaimResult
            or type(admission.query) is not CompiledQuery
            or admission.query.compiler_version != 2
            or admission.query.workspace_id != governance.workspace_id
            or admission.execution_authorized
            or admission.decision != "ALLOWED"
            or type(admission.reservation_id) is not int
            or admission.reservation_id < 1
            or preclaim.reservation_id != admission.reservation_id
            or preclaim.state != PreclaimState.REVALIDATED_ACTIVE
            or preclaim.execution_authorized or preclaim.reservation_claimed
            or not callable(resolve_current)):
        raise ValueError("Trusted non-authorizing ACTIVE preclaim result required")

    def current():
        values=resolve_current()
        if type(values) is not tuple or len(values)!=3:
            raise ValueError("Current trusted warehouse publication evidence required")
        binding,template,evidence=values
        if (type(binding) is not InternalDraftBinding
                or binding.executable is not False
                or tuple(m.physical_table for m in binding.models)!=admission.query.physical_tables
                or not _physical_pins_consistent(binding,governance.workspace_id)
                or not _matches_current(admission.query,template,evidence)):
            raise ValueError("Current warehouse compilation/publication differs")
        return binding

    # No state transition happens unless current publication evidence matches.
    initial_binding=current()
    row=governance.reservation(admission.reservation_id)
    if (row.get("status")!="ACTIVE"
            or row.get("workspace_id")!=governance.workspace_id
            or row.get("owner_key")!=governance.owner_key
            or row.get("consumer")!="SQL"
            or row.get("provider")!="BIGQUERY"
            or row.get("request_token")!=admission.query.request_token
            or row.get("intent_sha256")!=admission.query.intent_sha256):
        raise ValueError("Current owned ACTIVE warehouse reservation required")

    # Existing repository serializes the transition with PostgreSQL advisory
    # locks and ensures that an ACTIVE reservation is claimed only once.
    claimed=governance.claim(admission.reservation_id)

    try:
        binding=current()
        if binding != initial_binding:
            raise ValueError("WAREHOUSE_PUBLICATION_CHANGED_AFTER_CLAIM")
        inspect_claimed_warehouse_reservation(
            workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,
            consumer="SQL",
            reservation=claimed,binding=binding,query=admission.query,
        )
        # Re-fetch owned durable state to reject a terminal or identity change.
        latest=governance.reservation(admission.reservation_id)
        inspect_claimed_warehouse_reservation(
            workspace_id=governance.workspace_id,
            owner_key=governance.owner_key,
            consumer="SQL",
            reservation=latest,binding=binding,query=admission.query,
        )
    except Exception:
        # The provider has not been called by THIS function, but once claimed,
        # a different worker / future executor could have an uncertain outcome.
        # Never release, retry claim or finalize without recovery evidence.
        raise WarehouseClaimRecoveryRequired(admission.reservation_id) from None

    return WarehouseClaimRehearsal(
        reservation_id=admission.reservation_id,
        state=WarehouseClaimRehearsalState.CLAIMED_NO_EXECUTION,
    )
