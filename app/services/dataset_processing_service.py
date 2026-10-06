"""Dataset-scoped coordination; deliveries commit independently using existing stages."""
from contextlib import contextmanager
from contextvars import ContextVar

from app.db.database import get_connection
from app.db.processing_repository import list_dataset_deliveries
from app.services.processing_context_service import validate_scope, read_processing_context
from app.services.delivery_execution_service import continue_processing, delivery_lock

ELIGIBLE = {"READY_TO_PROCESS", "READY_FOR_SILVER", "READY_FOR_GOLD", "READY_TO_APPLY"}
_held_dataset = ContextVar('held_dataset',default=None)
TERMINAL = {"SUCCESS", "SUCCESS_WITH_WARNINGS"}


def eligible(context):
    if context.get("archived_at"):
        return False
    return context["status"] == "READY_TO_PROCESS" or (
        context["status"] in ELIGIBLE and context["can_continue"])


@contextmanager
def dataset_lock(workspace_id, dataset_id):
    if _held_dataset.get()==(workspace_id,dataset_id):
        yield
        return
    with get_connection() as conn:
        conn.autocommit = True
        namespace = conn.execute("SELECT current_schema()").fetchone()[0]
        key = f"{namespace}:datarise:dataset:{workspace_id}:{dataset_id}"
        if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]:
            raise RuntimeError("Dataset processing is already active")
        token=_held_dataset.set((workspace_id,dataset_id))
        try:
            yield
        finally:
            _held_dataset.reset(token)
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


def read_dataset_processing(workspace_id, dataset_id, user):
    validate_scope(user, workspace_id, dataset_id)
    deliveries = [{"source_file_name": row[1], "created_at": row[2], "size_bytes": row[3],
                   "context": read_processing_context(row[0], user, workspace_id, dataset_id)}
                  for row in list_dataset_deliveries(user["user_id"], workspace_id, dataset_id)]
    states = [d["context"] for d in deliveries]
    summary = {"total": len(states), "pending": sum(eligible(c) for c in states),
               "processing": sum(c["status"].endswith("_PROCESSING") for c in states),
               "awaiting_rules": sum(c["status"] == "AWAITING_RULES" for c in states),
               "successful": sum(c["status"] in TERMINAL for c in states),
               "failed": sum(c["status"].endswith("_FAILED") for c in states),
               "needs_attention": sum(c["status"].endswith("_FAILED") or c["status"] in
                                      ("AWAITING_RULES", "SUCCESS_WITH_WARNINGS", "DATASET_UPDATE_BLOCKED") for c in states)}
    return {"workspace_id": workspace_id, "dataset_id": dataset_id, "deliveries": deliveries, "summary": summary}


def process_pending(workspace_id, dataset_id, user):
    from app.services.processing_service import start_processing
    validate_scope(user, workspace_id, dataset_id)
    outcomes = []
    with dataset_lock(workspace_id, dataset_id):
        snapshot = read_dataset_processing(workspace_id, dataset_id, user)
        for delivery in snapshot["deliveries"]:
            initial = delivery["context"]
            if not eligible(initial):
                continue
            upload_id = initial["upload_request_id"]
            try:
                # Recheck under the delivery lock: a direct request may have advanced it.
                if initial["dataset_version_file_id"]:
                    with delivery_lock(initial["dataset_version_file_id"]):
                        current = read_processing_context(upload_id, user, workspace_id, dataset_id)
                        if not eligible(current):
                            continue
                        continue_processing(upload_id, user, workspace_id, dataset_id)
                else:
                    current = read_processing_context(upload_id, user, workspace_id, dataset_id)
                    if not eligible(current):
                        continue
                    start_processing(upload_id)
                    current = read_processing_context(upload_id, user, workspace_id, dataset_id)
                    if eligible(current):
                        continue_processing(upload_id, user, workspace_id, dataset_id)
                current = read_processing_context(upload_id, user, workspace_id, dataset_id)
                outcomes.append({"upload_request_id": upload_id, "status": current["status"],
                                 "needs_attention": current["status"] not in {"SUCCESS"}})
            except Exception:
                # Existing stage transactions persist failure. Never roll back another delivery.
                outcomes.append({"upload_request_id": upload_id, "status": "NEEDS_ATTENTION", "needs_attention": True})
        result = read_dataset_processing(workspace_id, dataset_id, user)
    result["operation"] = {"processed": len(outcomes),
                           "successful": sum(o["status"] in TERMINAL for o in outcomes),
                           "needs_attention": sum(o["needs_attention"] for o in outcomes), "outcomes": outcomes}
    return result
