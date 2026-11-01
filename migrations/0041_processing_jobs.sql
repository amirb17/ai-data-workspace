-- Durable orchestration only. Apply once, after 0040, with normal migration review.
-- No dispatch, stage execution, ownership backfill, or Exact V2 gate changes.
BEGIN;
CREATE TABLE processing_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requested_by BIGINT NOT NULL REFERENCES users(user_id),
    upload_id BIGINT NOT NULL REFERENCES upload_requests(upload_id),
    workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
    dataset_id BIGINT NOT NULL REFERENCES datasets(dataset_id),
    file_id BIGINT NOT NULL REFERENCES physical_files(file_id),
    operation VARCHAR(16) NOT NULL CHECK (operation IN ('BRONZE','CONTINUE','SILVER','GOLD')),
    idempotency_key VARCHAR(128) NOT NULL CHECK (length(btrim(idempotency_key)) > 0),
    status VARCHAR(16) NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED','CANCELLED')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    last_claimed_at TIMESTAMPTZ,
    recovery_count INTEGER NOT NULL DEFAULT 0 CHECK (recovery_count >= 0),
    failure_code VARCHAR(32) CHECK (failure_code IN
        ('SOURCE_INELIGIBLE','LEASE_EXPIRED','PROCESSING_FAILED','WORKER_INTERRUPTED')),
    result_code VARCHAR(32) CHECK (result_code IN ('COMPLETED','SUCCESS_WITH_WARNINGS')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ,
    UNIQUE (requested_by, operation, idempotency_key),
    CHECK (attempt_count <= max_attempts),
    CHECK ((status = 'RUNNING') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL)),
    CHECK ((status IN ('SUCCEEDED','FAILED','CANCELLED')) = (completed_at IS NOT NULL)),
    CHECK ((status = 'SUCCEEDED') = (result_code IS NOT NULL))
);
CREATE INDEX processing_jobs_retry_ready ON processing_jobs(available_at, created_at)
    WHERE status IN ('QUEUED','RETRY_WAIT');
CREATE INDEX processing_jobs_expired_leases ON processing_jobs(lease_expires_at)
    WHERE status = 'RUNNING';
COMMIT;
