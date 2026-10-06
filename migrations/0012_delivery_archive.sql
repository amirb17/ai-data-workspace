-- Logical lifecycle only: retain physical sources and every processing association.
ALTER TABLE upload_requests ADD COLUMN archived_at TIMESTAMPTZ;
ALTER TABLE upload_requests ADD COLUMN archived_by BIGINT REFERENCES users(user_id);
ALTER TABLE upload_requests ADD CONSTRAINT upload_archive_audit_check
    CHECK ((archived_at IS NULL) = (archived_by IS NULL));
