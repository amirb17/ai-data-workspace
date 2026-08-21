-- ============================================
-- 1. PHYSICAL FILES
-- Represents unique physical file/content
-- ============================================

-- =========================================================
-- AI DATA WORKSPACE - FILE MANAGEMENT SCHEMA
-- PostgreSQL
-- =========================================================


-- =========================================================
-- 1. PHYSICAL FILES
-- Stores information about the actual physical file.
-- One physical file can be referenced by multiple users.
-- =========================================================

CREATE TABLE physical_files (
    file_id BIGSERIAL PRIMARY KEY,

    file_name VARCHAR(255) NOT NULL,

    file_size BIGINT NOT NULL
        CHECK (file_size > 0),

    file_hash VARCHAR(64) NOT NULL UNIQUE,

    storage_path TEXT,

    status VARCHAR(30) NOT NULL DEFAULT 'UPLOADING'
        CHECK (
            status IN (
                'UPLOADING',
                'UPLOADED',
                'PROCESSING',
                'SUCCESS',
                'FAILED',
                'CRASHED'
            )
        ),

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- =========================================================
-- 2. UPLOAD REQUESTS
-- Represents each user's upload/request.
--
-- Multiple users can reference the same physical file.
-- =========================================================

CREATE TABLE upload_requests (
    upload_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    file_id BIGINT NOT NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'UPLOADING'
        CHECK (
            status IN (
                'UPLOADING',
                'UPLOADED',
                'PROCESSING',
                'SUCCESS',
                'FAILED',
                'CRASHED'
            )
        ),

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (file_id)
        REFERENCES physical_files(file_id)
        ON DELETE RESTRICT
);


-- =========================================================
-- 3. PROCESSING ATTEMPTS
-- Tracks every processing attempt and processing stage.
--
-- Example:
--
-- Attempt 1 -> BRONZE -> SUCCESS
-- Attempt 1 -> SILVER -> FAILED
-- Attempt 2 -> SILVER -> SUCCESS
-- Attempt 2 -> GOLD   -> SUCCESS
-- =========================================================

CREATE TABLE processing_attempts (
    attempt_id BIGSERIAL PRIMARY KEY,

    file_id BIGINT NOT NULL,

    attempt_number INT NOT NULL
        CHECK (attempt_number > 0),

    stage VARCHAR(30) NOT NULL
        CHECK (
            stage IN (
                'BRONZE',
                'SILVER',
                'GOLD'
            )
        ),

    status VARCHAR(30) NOT NULL
        CHECK (
            status IN (
                'PROCESSING',
                'SUCCESS',
                'FAILED',
                'CRASHED'
            )
        ),

    error_message TEXT,

    started_at TIMESTAMP,

    completed_at TIMESTAMP,

    FOREIGN KEY (file_id)
        REFERENCES physical_files(file_id)
        ON DELETE RESTRICT
);


-- =========================================================
-- INDEXES
-- =========================================================

-- Quickly find all uploads belonging to a file
CREATE INDEX idx_upload_requests_file_id
ON upload_requests(file_id);


-- Quickly find uploads by user
CREATE INDEX idx_upload_requests_user_id
ON upload_requests(user_id);


-- Quickly find processing attempts for a file
CREATE INDEX idx_processing_attempts_file_id
ON processing_attempts(file_id);


-- Quickly find attempts by stage/status
CREATE INDEX idx_processing_attempts_stage_status
ON processing_attempts(stage, status);