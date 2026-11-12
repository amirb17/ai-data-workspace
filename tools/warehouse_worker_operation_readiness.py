"""Read-only live permission readiness for warehouse worker service operations.

Worker health only verifies identities. It does NOT prove that recovery and
settlement SQL can acquire row locks or read canonical source tables.
This audit checks the effective table privileges for the exact known service
prerequisites, without running a query attempt, obtaining a row lock, modifying
DB objects, or printing credentials.

The audit is conservative: PASS means this specific access matrix was met,
NOT that runtime enablement is authorized. Missing privileges are reported as
named service blockers. Use before any real worker operation.

  python -m tools.warehouse_worker_operation_readiness
"""
from __future__ import annotations

import json
import sys

import psycopg

from tools.warehouse_deployment_boundary import _dsn
from tools.warehouse_worker_acl import ROLES

P = "public"
Q = "datarise_warehouse_dev"

# Each entry: (qualified table, demanded effective privilege, why).
# PostgreSQL SELECT ... FOR UPDATE and FOR SHARE require UPDATE privilege.
# Data reads of the original authenticated context have to remain scoped.
BASE = (
    (Q+".warehouse_query_dispatch_checkpoints", "SELECT", "checkpoint"),
    (Q+".warehouse_query_attempt_registry", "SELECT", "checkpoint_identity"),
    (Q+".warehouse_query_attempt_registry", "UPDATE", "checkpoint_for_update"),
    (Q+".warehouse_query_attempt_publication_pins", "SELECT", "publication_pins"),
    (Q+".warehouse_query_send_intents", "SELECT", "send_intent"),
    (Q+".warehouse_query_send_intents", "UPDATE", "send_intent_for_update"),
    (Q+".warehouse_query_send_journal", "SELECT", "send_journal"),
    (P+".query_governance_reservations", "SELECT", "reservation"),
    (P+".query_governance_reservations", "UPDATE", "reservation_for_update"),
)
LIVE_CONTEXT = (
    (P+".users", "SELECT", "authenticated_user"),
    (P+".users", "UPDATE", "authenticated_user_for_share"),
    (P+".auth_sessions", "SELECT", "session"),
    (P+".auth_sessions", "UPDATE", "session_for_share"),
    (P+".workspaces", "SELECT", "workspace"),
    (P+".workspaces", "UPDATE", "workspace_for_share"),
    (P+".warehouse_resources", "SELECT", "serving_resource"),
    (P+".warehouse_resources", "UPDATE", "serving_resource_for_share"),
    (P+".warehouse_publication_heads", "SELECT", "publication_head"),
    (P+".warehouse_publication_heads", "UPDATE", "publication_head_for_share"),
    (P+".warehouse_serving_materializations", "SELECT", "serving_receipt"),
    (P+".warehouse_serving_materializations", "UPDATE", "serving_receipt_for_share"),
    (Q+".warehouse_query_attempt_logical_drafts", "SELECT", "draft"),
    (Q+".warehouse_query_attempt_logical_drafts", "UPDATE", "draft_for_share"),
    (P+".warehouse_model_sources", "SELECT", "model_source"),
    (P+".warehouse_model_sources", "UPDATE", "model_source_for_share"),
    (P+".datasets", "SELECT", "source_dataset"),
    (P+".datasets", "UPDATE", "source_dataset_for_share"),
)
REQUIREMENTS = {
    "provider_submission": BASE + LIVE_CONTEXT + (
        (Q+".warehouse_query_request_envelopes", "SELECT", "pinned_envelope"),
        (Q+".warehouse_query_provider_policies", "SELECT", "pinned_policy"),
        (Q+".warehouse_query_send_journal", "INSERT", "record_boundary"),
    ),
    "recovery_attestation": BASE + (
        (Q+".warehouse_query_send_journal", "INSERT", "recovery_lease_journal"),
        (Q+".warehouse_query_recovery_attestations", "INSERT", "attestation"),
    ),
    # Accounting still takes the governance reservation FOR UPDATE when
    # writing the durable usage ledger. Immutable attempt and send-intent
    # rows are inspected beneath the shared global advisory transaction.
    "accounting": tuple(
        entry for entry in BASE
        if not (entry[1] == "UPDATE" and entry[0] in {
            Q+".warehouse_query_attempt_registry",
            Q+".warehouse_query_send_intents",
        })
    ) + (
        (P+".workspaces", "SELECT", "canonical_owner"),
        (Q+".warehouse_query_request_envelopes", "SELECT", "pinned_request_evidence"),
        (Q+".warehouse_query_provider_policies", "SELECT", "attestation_policy"),
        (Q+".warehouse_query_recovery_attestations", "SELECT", "attested_proof"),
        (P+".query_governance_usage", "SELECT", "settled_ledger_readback"),
        (P+".query_governance_usage", "INSERT", "settlement_ledger"),
    ),
    # Result authorization is inspection-only. It shares the transaction's
    # advisory lock but requests NO row UPDATE privileges.
    "result_authorization": tuple(
        entry for entry in BASE if entry[1] != "UPDATE"
    ) + (
        (P+".query_governance_usage", "SELECT", "settled_ledger"),
        (Q+".warehouse_query_request_envelopes", "SELECT", "pinned_request"),
        (Q+".warehouse_query_recovery_attestations", "SELECT", "attested_proof"),
    ),
}


def inspect(conn):
    report = {}
    for duty, role in ROLES.items():
        blockers=[]
        for table, privilege, usage in REQUIREMENTS[duty]:
            row=conn.execute(
                "SELECT has_table_privilege(%s, %s, %s)",
                (role,table,privilege),
            ).fetchone()
            if row is None or row[0] is not True:
                blockers.append({
                    "relation":table,
                    "privilege":privilege,
                    "required_by":usage,
                })
        report[duty]={
            "permissions_ready":not blockers,
            "missing_privileges":blockers,
            "requirements_checked":len(REQUIREMENTS[duty]),
        }
    return {
        "worker_health_is_not_operation_readiness":True,
        "all_operations_permission_ready":all(
            x["permissions_ready"] for x in report.values()
        ),
        "query_execution_enabled":False,
        "workers":report,
    }


def main():
    try:
        with psycopg.connect(_dsn()) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            result=inspect(conn)
        print(json.dumps(result,sort_keys=True,indent=2))
        return 0 if result["all_operations_permission_ready"] else 3
    except Exception as exc:
        # psycopg exceptions may contain connection strings.
        print("WORKER_OPERATION_READINESS_BLOCKED:",
              type(exc).__name__,file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
