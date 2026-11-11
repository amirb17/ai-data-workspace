"""414F0-B8: fail-closed assessment of real warehouse function/trigger catalog.

Only consumes B6 catalog result rows. Never creates roles, grants permissions,
alters objects or authorizes a query/physical cleanup operation.
"""
from dataclasses import dataclass
from typing import Mapping, Sequence

EXPECTED_FUNCTIONS = frozenset({
    "warehouse_query_attempt_registry_guard",
    "warehouse_query_attempt_publication_guard",
    "warehouse_query_attempt_reservation_link_guard",
    "warehouse_query_linked_pin_insert_guard",
    "warehouse_query_retirement_fence_guard",
    "warehouse_query_retirement_link_guard",
    "warehouse_query_dispatch_record_guard",
    "warehouse_query_dispatch_revoke_notice",
    "warehouse_query_send_intent_guard",
    "warehouse_query_send_journal_guard",
    "warehouse_query_send_initial_event_guard",
    "warehouse_query_recovery_attestation_guard",
    "warehouse_query_provider_policy_guard",
    "warehouse_query_publication_dependency_present",
})
EXPECTED_TRIGGERS = frozenset({
    "warehouse_query_attempt_registry_validate",
    "warehouse_query_attempt_publication_validate",
    "warehouse_query_attempt_reservation_link_validate",
    "warehouse_query_linked_pin_insert_validate",
    "warehouse_query_retirement_fence_validate",
    "a_warehouse_query_retirement_link_validate",
    "warehouse_query_dispatch_checkpoint_guard",
    "warehouse_query_dispatch_revocation_guard",
    "warehouse_dispatch_session_revoke",
    "warehouse_dispatch_account_revoke",
    "warehouse_dispatch_workspace_revoke",
    "warehouse_dispatch_publication_revoke",
    "warehouse_dispatch_resource_revoke",
    "warehouse_dispatch_source_revoke",
    "warehouse_dispatch_model_revoke",
    "warehouse_dispatch_version_revoke",
    "warehouse_send_intent_guard",
    "warehouse_send_journal_guard",
    "warehouse_recovery_attestation_guard",
    "warehouse_provider_policy_guard",
})


@dataclass(frozen=True)
class CatalogAssessment:
    verdict: str
    blockers: tuple[str, ...]
    functions_checked: int
    triggers_checked: int
    public_execute_functions: tuple[str, ...]

    @property
    def permissions_approved(self) -> bool:
        return False


def assess_catalog(
    identity: Sequence[Mapping],
    functions: Sequence[Mapping],
    triggers: Sequence[Mapping],
) -> CatalogAssessment:
    blockers: set[str] = set()
    if len(identity) != 1:
        blockers.add("INVALID_CATALOG_SCOPE")
    else:
        name = identity[0].get("audited_schema")
        if (not isinstance(name, str) or not name.startswith("test_warehouse_")
                or not name.removeprefix("test_warehouse_").isalnum()):
            blockers.add("INVALID_CATALOG_SCOPE")

    seen_functions: set[str] = set()
    public_execute: set[str] = set()
    for row in functions:
        name = row.get("expected_function")
        if name not in EXPECTED_FUNCTIONS or name in seen_functions:
            blockers.add("FUNCTION_COVERAGE_INCOMPLETE_OR_AMBIGUOUS")
            continue
        seen_functions.add(name)
        if row.get("exists") is not True:
            blockers.add("FUNCTION_MISSING")
        if row.get("security_definer") is not False:
            blockers.add("SECURITY_DEFINER_OR_UNKNOWN")
        if row.get("public_execute") is True:
            public_execute.add(name)
            blockers.add("PUBLIC_EXECUTE_REQUIRES_REVIEW")
        elif row.get("public_execute") is not False:
            blockers.add("FUNCTION_EXECUTE_ACL_UNKNOWN")
        if not isinstance(row.get("function_owner"), str) or not row["function_owner"]:
            blockers.add("FUNCTION_OWNER_UNKNOWN")
        # An absent fixed function search_path is not by itself proof of
        # exploitability; review it before defining roles or SECURITY DEFINER.
        settings = row.get("function_settings")
        if settings is None or not (
            isinstance(settings, (list, tuple))
            and any(isinstance(item, str) and item.startswith("search_path=") for item in settings)
        ):
            blockers.add("FUNCTION_SEARCH_PATH_REQUIRES_REVIEW")
    if seen_functions != EXPECTED_FUNCTIONS or len(functions) != len(EXPECTED_FUNCTIONS):
        blockers.add("FUNCTION_COVERAGE_INCOMPLETE_OR_AMBIGUOUS")

    seen_triggers: set[str] = set()
    for row in triggers:
        name = row.get("expected_trigger")
        if name not in EXPECTED_TRIGGERS or name in seen_triggers:
            blockers.add("TRIGGER_COVERAGE_INCOMPLETE_OR_AMBIGUOUS")
            continue
        seen_triggers.add(name)
        if row.get("exists") is not True:
            blockers.add("TRIGGER_MISSING")
        if row.get("enabled_mode") not in ("O", "A"):
            blockers.add("TRIGGER_DISABLED_OR_UNKNOWN")
        if (not isinstance(row.get("table_name"), str) or not row["table_name"]
                or not isinstance(row.get("function_target"), str) or not row["function_target"]):
            blockers.add("TRIGGER_TARGET_UNKNOWN")
    if seen_triggers != EXPECTED_TRIGGERS or len(triggers) != len(EXPECTED_TRIGGERS):
        blockers.add("TRIGGER_COVERAGE_INCOMPLETE_OR_AMBIGUOUS")
    if not blockers:
        blockers.add("AUTHENTICATED_ROLE_AND_TRANSITIVE_PRIVILEGE_REVIEW_REQUIRED")

    return CatalogAssessment(
        verdict="NO_GO", blockers=tuple(sorted(blockers)),
        functions_checked=len(functions), triggers_checked=len(triggers),
        public_execute_functions=tuple(sorted(public_execute)),
    )
