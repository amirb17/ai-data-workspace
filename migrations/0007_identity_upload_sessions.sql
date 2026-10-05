-- Phase 4A: durable development identities and upload sessions.
-- Apply after 0001..0006. No automatic backfill/claim of legacy owners.
BEGIN;
CREATE TABLE users (
    user_id BIGSERIAL PRIMARY KEY,
    owner_key VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE upload_requests ALTER COLUMN file_id DROP NOT NULL;
ALTER TABLE upload_requests ADD COLUMN staging_object_key TEXT UNIQUE;
ALTER TABLE upload_requests ADD COLUMN source_file_name VARCHAR(255);
ALTER TABLE upload_requests ADD COLUMN expected_file_size BIGINT;
ALTER TABLE upload_requests ADD COLUMN content_type VARCHAR(255);
ALTER TABLE upload_requests ADD COLUMN completed_at TIMESTAMP;
ALTER TABLE upload_requests ADD COLUMN completion_result JSONB;
ALTER TABLE upload_requests ADD COLUMN last_error TEXT;
ALTER TABLE upload_requests DROP CONSTRAINT upload_requests_status_check;
ALTER TABLE upload_requests ADD CONSTRAINT upload_requests_status_check
    CHECK (status IN ('INITIATED','UPLOADING','UPLOADED','PROCESSING','SUCCESS','FAILED','CRASHED'));
ALTER TABLE upload_requests ADD CONSTRAINT upload_session_context_check CHECK (
    staging_object_key IS NULL OR (
        workspace_id IS NOT NULL AND dataset_id IS NOT NULL AND source_file_name IS NOT NULL
        AND expected_file_size > 0
    )
);
ALTER TABLE upload_requests ADD CONSTRAINT upload_session_completion_check CHECK (
    completed_at IS NULL OR (file_id IS NOT NULL AND completion_result IS NOT NULL)
);
COMMIT;
