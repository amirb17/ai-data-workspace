-- Read-only aggregate diagnostics for a separately approved observer identity.
-- Never execute through queue messages or the worker/dispatcher identity.
-- This task does not execute these queries against any database.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SELECT status, count(*) AS jobs FROM processing_jobs GROUP BY status ORDER BY status;
SELECT count(*) AS due_unclaimed, min(o.available_at) AS oldest_due
FROM processing_dispatch_outbox o JOIN processing_jobs j USING (job_id)
WHERE j.status IN ('QUEUED','RETRY_WAIT','RUNNING') AND o.delivered_at IS NULL
  AND o.available_at <= clock_timestamp()
  AND (o.claim_until IS NULL OR o.claim_until <= clock_timestamp());
SELECT count(*) AS expired_running
FROM processing_jobs WHERE status='RUNNING' AND lease_expires_at<=clock_timestamp();
COMMIT;
