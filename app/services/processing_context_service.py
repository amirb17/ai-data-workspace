"""Ownership-checked, public processing/rule context. No stage orchestration."""
from contextlib import contextmanager

from app.db.database import repository_transaction
from app.db.dataset_repository import (
    get_dataset_version_file_by_id, get_upload_processing_association,
    get_dataset_by_workspace,
)
from app.db.file_repository import (
    get_upload_request_by_id, get_physical_file_by_id,
    get_active_processing_attempt, get_active_business_rules_for_dataset_version,
)
from app.services.file_service import validate_upload_context
from app.services.business_rule_service import get_business_rule_questions
from app.db.processing_repository import get_approved_policy, get_delivery_attempts, get_delivery_issues
from app.db.file_repository import (get_latest_successful_dq_run_for_dataset_version_file,
    get_successful_gold_run_for_dq_run_and_dataset_version_file, get_dataset_profile_summary)


class StaleRuleVersion(ValueError):
    pass


def rule_state(status):
    finalized = {"READY_FOR_SILVER", "SILVER_PROCESSING", "SILVER_FAILED", "READY_FOR_GOLD",
                 "GOLD_PROCESSING", "GOLD_FAILED", "SUCCESS", "SUCCESS_WITH_WARNINGS"}
    return "FINALIZED" if status in finalized else "DRAFT"


def validate_scope(user, workspace_id, dataset_id):
    if workspace_id is None or dataset_id is None:
        raise ValueError("Workspace and dataset are required")
    dataset = get_dataset_by_workspace(workspace_id, dataset_id)
    if dataset is None or dataset[3] != user["owner_key"]:
        raise PermissionError("Dataset access denied")
    validate_upload_context(user["user_id"], workspace_id, dataset_id)


def owned_upload(upload_id, user, workspace_id=None, dataset_id=None):
    upload = get_upload_request_by_id(upload_id)
    if upload is None:
        raise LookupError("Upload not found")
    if upload[1] != user["user_id"]:
        raise PermissionError("Upload access denied")
    if workspace_id is not None and upload[3] != workspace_id:
        raise PermissionError("Workspace context mismatch")
    if dataset_id is not None and upload[4] != dataset_id:
        raise PermissionError("Dataset context mismatch")
    validate_scope(user, upload[3], upload[4])
    # Durable completion retains the legacy database status UPLOADED.
    if upload[5] != "UPLOADED" or upload[2] is None:
        raise ValueError("Upload must be completed before Bronze or rule review")
    if get_physical_file_by_id(upload[2]) is None:
        raise ValueError("Source file is unavailable")
    return upload


def owned_rule_context(association_id, user, workspace_id=None, dataset_id=None):
    context = get_dataset_version_file_by_id(association_id)
    if context is None:
        raise LookupError("Processing context not found")
    if workspace_id is not None and workspace_id != context[6]:
        raise PermissionError("Workspace context mismatch")
    if dataset_id is not None and dataset_id != context[5]:
        raise PermissionError("Dataset context mismatch")
    validate_scope(user, context[6], context[5])
    return context


def public_rules(association_id, user, workspace_id=None, dataset_id=None):
    context = owned_rule_context(association_id, user, workspace_id, dataset_id)
    result = get_business_rule_questions(association_id)
    return {**result, "workspace_id": context[6], "dataset_id": context[5],
            "rule_state": rule_state(context[3])}


def read_processing_context(upload_id, user, workspace_id=None, dataset_id=None):
    upload = owned_upload(upload_id, user, workspace_id, dataset_id)
    association = get_upload_processing_association(upload[4], upload[2])
    active = get_active_processing_attempt(upload[2])
    status = association[3] if association else "READY_TO_PROCESS"
    if active and active[3] == "BRONZE":
        status = "BRONZE_PROCESSING"
    elif not association and get_physical_file_by_id(upload[2])[5] == "FAILED":
        status = "BRONZE_FAILED"
    active_count = len(get_active_business_rules_for_dataset_version(association[1])) if association else 0
    pin = association[6] if association else None
    policy = get_approved_policy(association[1], pin) if pin else []
    if policy:
        active_count = len(policy)  # Count the applied policy, not a newer mutable draft.
    attempts = get_delivery_attempts(association[0] if association else None, upload[2])
    stages = {a[1]: a[2] for a in attempts}
    latest = max(attempts, key=lambda a: a[0]) if attempts else None
    dq = get_latest_successful_dq_run_for_dataset_version_file(association[0]) if association else None
    if dq and dq[3] != pin:
        dq = None
    gold = get_successful_gold_run_for_dq_run_and_dataset_version_file(association[0], dq[0]) if dq else None
    summary = get_dataset_profile_summary(upload[2])
    skip = None
    if status == "SUCCESS_WITH_WARNINGS":
        from app.db.processing_repository import get_gold_skip
        skip = get_gold_skip(association[0])
    safe_error = None
    if latest and latest[2] in ("FAILED", "CRASHED") and not skip:
        safe_error = {"BRONZE": "Bronze inspection failed.", "SILVER": "Silver validation failed.",
                      "GOLD": "Gold publication failed."}.get(latest[1], "Processing could not be completed.")
    return {
        "upload_request_id": upload[0], "workspace_id": upload[3], "dataset_id": upload[4],
        "file_id": upload[2], "status": status,
        "dataset_version_file_id": association[0] if association else None,
        "dataset_version_id": association[1] if association else None,
        "dataset_version_number": association[4] if association else None,
        "rule_version": (association[6] if association[6] is not None else association[5]) if association else None,
        "current_rule_version": association[5] if association else None,
        "rules_reused": association[7] if association else False,
        "rule_state": rule_state(association[3]) if association else None,
        "active_rule_count": active_count,
        "silver_can_proceed": status in ("READY_FOR_SILVER", "SILVER_FAILED") and bool(policy),
        "can_continue": status in ("READY_FOR_SILVER", "SILVER_FAILED", "READY_FOR_GOLD", "GOLD_FAILED")
                        and bool(policy),
        "stages": {"bronze": "SUCCESS" if association else stages.get("BRONZE", "PENDING"),
                   "rules": rule_state(status) if association else "PENDING",
                   "silver": "SUCCESS" if dq else stages.get("SILVER", "PENDING"),
                   "gold": "SKIPPED" if skip and skip[0] else "SUCCESS" if gold and status == "SUCCESS" else stages.get("GOLD", "PENDING")},
        "latest_attempt": {"id": latest[0], "stage": latest[1], "status": latest[2],
                           "started_at": latest[3], "completed_at": latest[4]} if latest else None,
        "started_at": min((a[3] for a in attempts if a[3]), default=None),
        "completed_at": skip[1] if skip else latest[4] if latest and status == "SUCCESS" else None,
        "gold_skip_reason": "No valid rows were available for Gold publication." if skip and skip[0] else None,
        "error_summary": safe_error,
        "input_rows": dq[4] if dq else summary[2] if summary else None,
        "valid_rows": dq[5] if dq else None, "rejected_rows": dq[6] if dq else None,
        "output_rows": 0 if skip and skip[0] else gold[5] if gold and status == "SUCCESS" else None,
        "column_count": summary[3] if summary else None,
        "duplicate_rows": None, "updated_rows": None,
        "quarantine_available": bool(dq and dq[8]),
        "issue_summary": [{"rule_type": r[0] if r[0] in ("NOT_NULL", "DATA_TYPE", "ALLOW_NEGATIVE", "UNIQUE", "DUPLICATE", "INVALID_NUMERIC", "INVALID_DATETIME") else "VALIDATION",
                           "violation_count": r[1]} for r in get_delivery_issues(dq[0])] if dq else [],
    }


@contextmanager
def rule_mutation(association_id, user, expected_rule_version=None, workspace_id=None, dataset_id=None):
    """Lock the schema's rule version; answer writes and version bump commit together."""
    with repository_transaction() as conn:
        context = owned_rule_context(association_id, user, workspace_id, dataset_id)
        with conn.cursor() as cursor:
            cursor.execute("SELECT rule_version FROM dataset_versions WHERE dataset_version_id = %s FOR UPDATE",
                           (context[1],))
            current_version = cursor.fetchone()[0]
            cursor.execute("SELECT 1 FROM processing_attempts pa JOIN dataset_version_files dvf "
                           "ON dvf.dataset_version_file_id = pa.dataset_version_file_id "
                           "WHERE dvf.dataset_version_id = %s AND pa.status = 'PROCESSING' LIMIT 1", (context[1],))
            if cursor.fetchone():
                raise StaleRuleVersion("Rules are in use by a processing attempt")
        if expected_rule_version is not None and current_version != expected_rule_version:
            raise StaleRuleVersion("Rule version changed; reload before saving")
        yield context
