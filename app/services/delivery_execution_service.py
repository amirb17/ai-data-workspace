"""One-delivery coordination over the existing Silver and Gold processors."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from app.db.database import get_connection, repository_transaction
from app.db.dataset_repository import get_dataset_version_file_by_id, update_dataset_version_file_status
from app.db.file_repository import get_active_processing_attempt_for_dataset_version_file, complete_processing_attempt

_locked_delivery = ContextVar("locked_delivery", default=None)


@contextmanager
def delivery_lock(association_id):
    if _locked_delivery.get() == association_id:
        yield
        return
    # Session lock spans storage I/O; stage metadata commits remain visible to polling.
    with get_connection() as conn:
        conn.autocommit = True
        namespace = conn.execute("SELECT current_schema()").fetchone()[0]
        key = f"{namespace}:datarise:delivery:{association_id}"
        if not conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]:
            raise RuntimeError("Delivery processing is already active")
        token = _locked_delivery.set(association_id)
        try:
            active = get_active_processing_attempt_for_dataset_version_file(association_id)
            if active:
                # The lock is free: a prior worker ended without completing its attempt.
                with repository_transaction():
                    complete_processing_attempt(active[0], "CRASHED", "Processing interrupted; retry resumed")
                    context = get_dataset_version_file_by_id(association_id)
                    if context[3] not in ("SUCCESS", "SUCCESS_WITH_WARNINGS"):
                        update_dataset_version_file_status(association_id, f"{active[3]}_FAILED")
            yield
        finally:
            _locked_delivery.reset(token)
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


def guarded_delivery(operation):
    @wraps(operation)
    def guarded(dataset_version_file_id, bucket_name):
        with delivery_lock(dataset_version_file_id):
            return operation(dataset_version_file_id, bucket_name)
    return guarded


def continue_processing(upload_id, user, workspace_id=None, dataset_id=None, stage=None):
    from app.config import S3_BUCKET_NAME
    from app.services.processing_context_service import owned_upload, owned_rule_context, read_processing_context
    from app.db.dataset_repository import get_upload_processing_association
    from app.services.processing_service import run_silver_processing, run_gold_processing
    from app.services.append_application_service import selected_policy,apply_append
    from app.services.dataset_processing_service import dataset_lock
    from app.services.delivery_lifecycle_service import lifecycle_lock,require_active
    from contextlib import nullcontext
    upload = owned_upload(upload_id, user, workspace_id, dataset_id)
    association = get_upload_processing_association(upload[4], upload[2])
    if association is None:
        raise ValueError("Bronze must complete before continuing")
    owned_rule_context(association[0], user, upload[3], upload[4])
    load_policy=selected_policy(upload[4],upload_id)
    with (dataset_lock(upload[3],upload[4]) if load_policy else nullcontext()), lifecycle_lock(upload_id), delivery_lock(association[0]):
        require_active(upload_id)
        current = get_dataset_version_file_by_id(association[0])
        if not load_policy and current[3] in ("SUCCESS", "SUCCESS_WITH_WARNINGS"):
            return read_processing_context(upload_id, user, workspace_id, dataset_id)
        if stage != "GOLD":
            run_silver_processing(association[0], S3_BUCKET_NAME)
        if load_policy:
            apply_append(upload[3],upload[4],user,upload_id)
        if stage != "SILVER":
            run_gold_processing(association[0], S3_BUCKET_NAME)
    return read_processing_context(upload_id, user, workspace_id, dataset_id)
