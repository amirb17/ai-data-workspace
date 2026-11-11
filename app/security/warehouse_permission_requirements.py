"""414F0-B4: reviewed *requirements*, not GRANT statements or runtime permissions.

The SQL-authenticated controller currently runs through the general database
connection. These are narrow data-access requirements for later separation of
roles. This module cannot authorize an operation or modify PostgreSQL.
"""
from dataclasses import dataclass

from app.security.warehouse_privilege_assessment import TABLES

READ = "SELECT"
APPEND = "INSERT"
# These are logical internal controllers, NOT PostgreSQL role names.
CONTROLLERS = frozenset({
    "authenticated_checkpoint", "provider_policy_prepare",
    "recovery_observer", "retention_inspector",
})


@dataclass(frozen=True)
class Requirement:
    controller: str
    table: str
    privilege: str
    review_status: str = "DRAFT_NOT_GRANTED"

    def __post_init__(self):
        if (self.controller not in CONTROLLERS or self.table not in TABLES
                or self.privilege not in (READ, APPEND)
                or self.review_status != "DRAFT_NOT_GRANTED"):
            raise ValueError("Unreviewed warehouse privilege requirement")


# Not a deployed role matrix; actual code may use additional tables, column
# privileges, UPDATE row locks, sequences, trigger/function and role inheritance.
# No one should translate this list into a GRANT script without B4/B5 review.
REQUIREMENTS = (
    Requirement("authenticated_checkpoint", "warehouse_query_attempt_publication_pins", READ),
    Requirement("authenticated_checkpoint", "warehouse_query_attempt_reservation_links", READ),
    Requirement("authenticated_checkpoint", "warehouse_query_dispatch_checkpoints", READ),
    Requirement("provider_policy_prepare", "warehouse_query_dispatch_checkpoints", READ),
    Requirement("provider_policy_prepare", "warehouse_query_send_intents", READ),
    Requirement("provider_policy_prepare", "warehouse_query_attempt_publication_pins", READ),
    Requirement("provider_policy_prepare", "warehouse_query_provider_policies", READ),
    Requirement("provider_policy_prepare", "warehouse_query_provider_policies", APPEND),
    Requirement("recovery_observer", "warehouse_query_dispatch_checkpoints", READ),
    Requirement("recovery_observer", "warehouse_query_send_intents", READ),
    Requirement("recovery_observer", "warehouse_query_send_journal", READ),
    Requirement("recovery_observer", "warehouse_query_send_journal", APPEND),
    Requirement("recovery_observer", "warehouse_query_recovery_attestations", READ),
    Requirement("recovery_observer", "warehouse_query_recovery_attestations", APPEND),
    Requirement("recovery_observer", "warehouse_query_provider_policies", READ),
    Requirement("retention_inspector", "warehouse_query_attempt_publication_pins", READ),
    Requirement("retention_inspector", "warehouse_query_dispatch_checkpoints", READ),
    Requirement("retention_inspector", "warehouse_query_publication_retirement_fences", READ),
)


@dataclass(frozen=True)
class MatrixReview:
    status: str
    controller: str
    requirements: tuple[Requirement, ...]
    blockers: tuple[str, ...]

    @property
    def grants_authorized(self) -> bool:
        return False


def inspect_controller(controller: str) -> MatrixReview:
    if controller not in CONTROLLERS:
        raise ValueError("Unknown warehouse internal controller")
    matching = tuple(r for r in REQUIREMENTS if r.controller == controller)
    # Fail closed even if requirement matching is complete: no granted rights,
    # no verified dependency closure, and no authenticated role isolation.
    return MatrixReview(
        status="NO_GO",
        controller=controller,
        requirements=matching,
        blockers=(
            "EFFECTIVE_ROLE_PRIVILEGES_NOT_VERIFIED",
            "FUNCTION_TRIGGER_LOCK_SEQUENCE_DEPENDENCIES_NOT_REVIEWED",
            "SEPARATE_RUNTIME_ROLE_AUTHENTICATION_NOT_TESTED",
        ),
    )
