"""Private scheduling repository. Proposed 0044 is required; no fallback route.

Calls compose with existing savepoints. Dispatch/reconcile services isolate their
context to guarantee each database operation commits before external calls.
No API route, deployed grants, provider SDK or production bootstrap is provided.
"""
from dataclasses import asdict
import json
from uuid import UUID

from app.db import database
from app.processing_worker.batch_scheduling import Runtime, Submission


class BatchRepository:
    def admit(self, actor, upload, key, runtime: Runtime, *, max_submissions=3):
        if not isinstance(runtime, Runtime) or type(max_submissions) is not int or not 1 <= max_submissions <= 3:
            raise ValueError('Trusted runtime and bounded budget required')
        with database.repository_savepoint() as conn:
            job = conn.execute('SELECT task20_admit(%s,%s,%s,%s)', (actor, upload, runtime.stage, key)).fetchone()[0]
            conn.execute('SELECT task158c_route(%s,%s::jsonb,%s,%s)',
                         (job, json.dumps(asdict(runtime)), runtime.fingerprint, max_submissions))
            return job

    @staticmethod
    def _intent(row):
        if row is None:
            return None
        submission, job, token, configuration, fingerprint = row
        runtime = Runtime(**configuration)
        if runtime.fingerprint != fingerprint:
            raise ValueError('Persisted runtime fingerprint mismatch')
        return Submission(submission, job, token, runtime, fingerprint)

    def claim(self):
        with database.repository_savepoint() as conn:
            return self._intent(conn.execute('SELECT * FROM task158c_claim()').fetchone())

    def get(self, submission_id):
        with database.repository_savepoint() as conn:
            return self._intent(conn.execute('''SELECT s.submission_id,s.job_id,s.claim_token,r.configuration,r.fingerprint
                FROM processing_batch_submissions s JOIN processing_batch_routes r USING(job_id)
                WHERE s.submission_id=%s''', (submission_id,)).fetchone())

    def response(self, intent, provider_id, kind):
        with database.repository_savepoint() as conn:
            return conn.execute('SELECT task158c_response(%s,%s,%s,%s)',
                                (intent.submission_id, intent.claim_token, provider_id, kind)).fetchone()[0]

    def reconcile(self, submission_id, observations):
        payload = json.dumps([dict(id=o.provider_id, status=o.status) for o in observations])
        with database.repository_savepoint() as conn:
            return conn.execute('SELECT task158c_reconcile(%s,%s::jsonb)', (submission_id, payload)).fetchone()[0]

    def due(self, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Bounded reconciliation scan required')
        with database.repository_savepoint() as conn:
            return [r[0] for r in conn.execute('''SELECT submission_id FROM processing_batch_submissions
                WHERE state<>'RETIRED' AND reconcile_at<=clock_timestamp() ORDER BY reconcile_at LIMIT %s''', (limit,))]

    def cancel(self, job_id, authenticated_actor):
        with database.repository_savepoint() as conn:
            return conn.execute('SELECT task158c_cancel(%s,%s)', (UUID(str(job_id)), authenticated_actor)).fetchone()[0]
