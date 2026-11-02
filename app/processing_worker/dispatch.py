"""Durable admission and at-least-once outbox delivery; all clients are injected."""
import json
from contextvars import Context

from app.db import database


def admit(user_id, upload_id, operation, idempotency_key, *, isolated_test=False):
    with database.get_connection() as conn:
        from app.processing_worker.runtime_security import require_runtime
        require_runtime(conn, 'api', isolated_test=isolated_test)
        return conn.execute('SELECT task20_admit(%s,%s,%s,%s)',
            (user_id, upload_id, operation, idempotency_key)).fetchone()[0]


def dispatch_one(client, queue_url, *, enabled=False, isolated_test=False):
    """Commit dispatch state independently of a caller's repository transaction."""
    if not enabled:
        raise RuntimeError('Dispatch requires deployment approval')
    return Context().run(_dispatch_one, client, queue_url, isolated_test)


def _dispatch_one(client, queue_url, isolated_test):
    with database.get_connection() as conn:
        from app.processing_worker.runtime_security import require_runtime
        require_runtime(conn, 'dispatch', isolated_test=isolated_test)
        claimed = conn.execute('SELECT * FROM task20_claim_dispatch()').fetchone()
    if claimed is None:
        return False
    job_id, token = claimed
    # A send timeout may have delivered the message. Leave the row reclaimable;
    # job UUID idempotency tolerates duplicates, never mark sent before send.
    client.send_message(QueueUrl=queue_url, MessageBody=json.dumps({'job_id': str(job_id)}))
    with database.get_connection() as conn:
        return conn.execute('SELECT task20_dispatched(%s,%s)', (job_id, token)).fetchone()[0]
