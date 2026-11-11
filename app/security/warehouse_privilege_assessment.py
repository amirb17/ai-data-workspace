"""414F0-B2: fail-closed evaluation of read-only PostgreSQL role inventory.

This assessment is diagnostic. It never grants a role, authorizes a provider
operation, or acts as an execution/cleanup permit.
"""
from dataclasses import dataclass
from typing import Mapping, Sequence

ROLES = frozenset({
    "datarise_authority_owner", "datarise_api", "datarise_worker",
    "datarise_verifier", "datarise_dispatch",
})
RUNTIME_ROLES = ROLES - {"datarise_authority_owner"}
TABLES = frozenset({
    "warehouse_query_dispatch_checkpoints",
    "warehouse_query_dispatch_revocations",
    "warehouse_query_send_intents",
    "warehouse_query_send_journal",
    "warehouse_query_recovery_attestations",
    "warehouse_query_provider_policies",
    "warehouse_query_attempt_publication_pins",
    "warehouse_query_attempt_reservation_links",
    "warehouse_query_publication_retirement_fences",
})
# This is NOT an allowlist of permitted writes. Any write requires separate,
# reviewed positive/negative role tests before B2 can be accepted.
WRITE_KEYS = ("can_insert", "can_update", "can_delete", "can_truncate")


@dataclass(frozen=True)
class PrivilegeAssessment:
    verdict: str
    blockers: tuple[str, ...]
    role_rows: int
    relation_rows: int
    membership_rows: int
    column_rows: int

    @property
    def approved(self) -> bool:
        # B2 cannot issue an authorization; that needs separate operational signoff.
        return False


def assess_inventory(
    roles: Sequence[Mapping],
    memberships: Sequence[Mapping],
    relations: Sequence[Mapping],
    columns: Sequence[Mapping],
    *,
    audited_schema: str,
) -> PrivilegeAssessment:
    """Assess raw five-result-set B1 inventory, without credentials or writes.

    Require complete rectangular coverage. Reject duplicates/ambiguous records,
    NULLs and missing identities. Emit stable codes, not raw sensitive catalog.
    """
    blockers: set[str] = set()
    if not isinstance(audited_schema, str) or not audited_schema or audited_schema == "public":
        blockers.add("UNREVIEWED_OR_PUBLIC_SCHEMA")

    seen_roles = set()
    for row in roles:
        name = row.get("role_name")
        if name not in ROLES or name in seen_roles:
            blockers.add("ROLE_INVENTORY_INCOMPLETE_OR_DUPLICATED")
            continue
        seen_roles.add(name)
        if row.get("exists") is not True:
            blockers.add("ROLE_NOT_PROVISIONED")
        if row.get("safe_attributes") is not True:
            blockers.add("UNSAFE_OR_UNVERIFIED_ROLE_ATTRIBUTES")
    if seen_roles != ROLES or len(roles) != len(ROLES):
        blockers.add("ROLE_INVENTORY_INCOMPLETE_OR_DUPLICATED")

    expected_members = {(a, b) for a in ROLES for b in ROLES if a != b}
    observed_members = set()
    for row in memberships:
        pair = (row.get("member"), row.get("target"))
        if pair not in expected_members or pair in observed_members:
            blockers.add("MEMBERSHIP_INVENTORY_INCOMPLETE_OR_DUPLICATED")
            continue
        observed_members.add(pair)
        if row.get("has_membership") is not False:
            blockers.add("CROSS_ROLE_MEMBERSHIP_OR_UNKNOWN")
    if observed_members != expected_members or len(memberships) != len(expected_members):
        blockers.add("MEMBERSHIP_INVENTORY_INCOMPLETE_OR_DUPLICATED")

    expected_relations = {(role, table) for role in RUNTIME_ROLES for table in TABLES}
    observed_relations = set()
    for row in relations:
        pair = (row.get("role_name"), row.get("relation_name"))
        if pair not in expected_relations or pair in observed_relations:
            blockers.add("TABLE_INVENTORY_INCOMPLETE_OR_DUPLICATED")
            continue
        observed_relations.add(pair)
        if row.get("relation_exists") is not True:
            blockers.add("WAREHOUSE_METADATA_TABLE_NOT_PRESENT")
        if row.get("owns_relation") is not False:
            blockers.add("RUNTIME_OWNERSHIP_OR_UNKNOWN")
        if row.get("can_select") not in (True, False):
            blockers.add("UNKNOWN_TABLE_READ_PRIVILEGE")
        for key in WRITE_KEYS:
            if row.get(key) is not False:
                blockers.add("WRITE_PRIVILEGE_REQUIRES_REVIEW_OR_UNKNOWN")
    if observed_relations != expected_relations or len(relations) != len(expected_relations):
        blockers.add("TABLE_INVENTORY_INCOMPLETE_OR_DUPLICATED")

    for row in columns:
        if ((row.get("role_name"), row.get("relation_name")) not in expected_relations
                or not isinstance(row.get("column_name"), str)
                or not row.get("column_name")):
            blockers.add("INVALID_COLUMN_PRIVILEGE_ROW")
        if row.get("can_insert") is not True and row.get("can_update") is not True:
            blockers.add("AMBIGUOUS_COLUMN_PRIVILEGE_ROW")
        blockers.add("COLUMN_WRITE_PRIVILEGE_REQUIRES_REVIEW")

    # Even a clean inventory is not sufficient: absent schema/USAGE, trigger,
    # function, IAM and credential checks prevent authorization.
    if not blockers:
        blockers.add("FURTHER_RUNTIME_ACCEPTANCE_REQUIRED")
    return PrivilegeAssessment(
        "NO_GO", tuple(sorted(blockers)), len(roles), len(relations),
        len(memberships), len(columns),
    )
