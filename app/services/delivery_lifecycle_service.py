"""Logical delivery archive; never deletes source bytes or execution history."""
from contextlib import contextmanager
from functools import wraps

from app.db.database import get_connection


class DeliveryArchiveConflict(RuntimeError):
    pass


@contextmanager
def lifecycle_lock(upload_id):
    # Shared by archive, Bronze and continuation, including pre-association gaps.
    with get_connection() as conn:
        conn.autocommit = True
        namespace = conn.execute("SELECT current_schema()").fetchone()[0]
        key = f"{namespace}:datarise:upload-lifecycle:{upload_id}"
        if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]:
            raise DeliveryArchiveConflict("Delivery processing is already active. This delivery cannot be archived yet.")
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


def archive_state(upload_id):
    with get_connection() as conn:
        row = conn.execute("SELECT archived_at, archived_by FROM upload_requests WHERE upload_id=%s", (upload_id,)).fetchone()
        return row if row else (None, None)


def require_active(upload_id):
    if archive_state(upload_id)[0] is not None:
        raise DeliveryArchiveConflict("Archived deliveries cannot be processed.")


def active_delivery(operation):
    @wraps(operation)
    def guarded(upload_id, *args, **kwargs):
        with lifecycle_lock(upload_id):
            require_active(upload_id)
            return operation(upload_id, *args, **kwargs)
    return guarded


def archive_delivery(upload_id, user, workspace_id, dataset_id):
    from app.services.processing_context_service import owned_upload, read_processing_context
    owned_upload(upload_id, user, workspace_id, dataset_id)
    with lifecycle_lock(upload_id):
        state = archive_state(upload_id)
        if state[0] is None:
            context = read_processing_context(upload_id, user, workspace_id, dataset_id)
            association = context["dataset_version_file_id"]
            # Do not use delivery_lock: its recovery path mutates interrupted attempts.
            with get_connection() as conn:
                conn.autocommit = True
                namespace = conn.execute("SELECT current_schema()").fetchone()[0]
                key = f"{namespace}:datarise:delivery:{association}"
                locked = False
                try:
                    if association:
                        locked = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]
                        if not locked:
                            raise DeliveryArchiveConflict("This delivery is currently processing and cannot be archived yet.")
                    active = conn.execute("""SELECT 1 FROM processing_attempts WHERE status='PROCESSING'
                        AND (dataset_version_file_id=%s OR (file_id=%s AND stage='BRONZE')) LIMIT 1""",
                        (association, context["file_id"])).fetchone()
                    if active or context["status"].endswith("_PROCESSING") or context["status"] == "PROCESSING":
                        raise DeliveryArchiveConflict("This delivery is currently processing and cannot be archived yet.")
                    conn.execute("UPDATE upload_requests SET archived_at=NOW(), archived_by=%s WHERE upload_id=%s AND archived_at IS NULL",
                                 (user["user_id"], upload_id))
                finally:
                    if locked:
                        conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
            state = archive_state(upload_id)
    return {"upload_request_id": upload_id, "workspace_id": workspace_id, "dataset_id": dataset_id,
            "archived_at": state[0], "archived_by": state[1]}
