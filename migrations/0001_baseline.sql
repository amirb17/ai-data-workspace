-- =========================================================
-- AI DATA WORKSPACE - BASELINE SCHEMA SNAPSHOT
-- =========================================================
--
-- This file documents the schema that already exists in the live
-- development database (ai-data-workspace-amir-dev / DATABASE_URL),
-- as reported via information_schema / pg_constraint / pg_indexes on
-- 2026-09-26. It is NOT meant to be run against that database.
--
-- Purpose:
--   1. Replace the previously stale app/db/schema.sql (which only
--      described physical_files/upload_requests/processing_attempts)
--      with an accurate description of the real schema.
--   2. Serve as the starting point ("migration 0001") for any future
--      schema change, which should be captured as a new numbered
--      file (0002_..., 0003_...) rather than made ad-hoc.
--   3. Allow the schema to be reproduced from scratch in a new
--      environment (staging, a teammate's machine, disaster
--      recovery).
--
-- Notes on fidelity:
--   - VARCHAR lengths not confirmed against pg_catalog are given a
--     reasonable default (VARCHAR(255) for names/descriptions,
--     VARCHAR(30) for short enum-style status/stage columns,
--     VARCHAR(64) for SHA-256 hex digests). Re-verify with
--     `\d+ <table>` in psql if exact fidelity is required.
--   - Nullability, defaults, CHECK constraints, UNIQUE constraints,
--     and indexes (including the partial unique index on
--     processing_attempts) are taken directly from the reported
--     pg_constraint / pg_indexes output and should be accurate.
--   - physical_files.dataset_version_id and its FK are the legacy,
--     currently-unused column described in the project brief. Do
--     not remove it here; removal is a separate, deliberate
--     migration once nothing depends on it.
-- =========================================================


-- =========================================================
-- 1. WORKSPACES
-- Top-level analytical/project boundary, scoped per owner.
-- =========================================================

CREATE TABLE workspaces (
    workspace_id BIGSERIAL PRIMARY KEY,

    workspace_name VARCHAR(255) NOT NULL,

    description TEXT,

    owner VARCHAR(255),

    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CONSTRAINT chk_workspace_status
        CHECK (status IN ('ACTIVE', 'ARCHIVED')),

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_workspace_owner_name UNIQUE (owner, workspace_name)
);

CREATE INDEX idx_workspaces_owner ON workspaces(owner);


-- =========================================================
-- 2. DATASETS
-- A logical business data entity, scoped to a workspace.
-- =========================================================

CREATE TABLE datasets (
    dataset_id BIGSERIAL PRIMARY KEY,

    dataset_name VARCHAR(255) NOT NULL,

    description TEXT,

    owner VARCHAR(255),

    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CONSTRAINT chk_datasets_status
        CHECK (status IN ('ACTIVE', 'ARCHIVED')),

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    workspace_id BIGINT,

    CONSTRAINT fk_datasets_workspace
        FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),

    CONSTRAINT uq_dataset_workspace_name UNIQUE (workspace_id, dataset_name)
);

CREATE INDEX idx_datasets_workspace_id ON datasets(workspace_id);


-- =========================================================
-- 3. DATASET VERSIONS
-- A version of a dataset, identified by its logical schema hash.
-- =========================================================

CREATE TABLE dataset_versions (
    dataset_version_id BIGSERIAL PRIMARY KEY,

    dataset_id BIGINT NOT NULL,

    version_number INT NOT NULL
        CONSTRAINT chk_dataset_version_number CHECK (version_number > 0),

    schema_hash VARCHAR(64),

    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CONSTRAINT chk_dataset_version_status
        CHECK (status IN ('ACTIVE', 'DEPRECATED')),

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT dataset_versions_dataset_id_fkey
        FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id),

    CONSTRAINT uq_dataset_version UNIQUE (dataset_id, version_number),
    CONSTRAINT uq_dataset_version_schema UNIQUE (dataset_id, schema_hash)
);


-- =========================================================
-- 4. PHYSICAL FILES
-- Canonical registry of unique physical file content
-- (deduplicated by SHA-256).
-- =========================================================

CREATE TABLE physical_files (
    file_id BIGSERIAL PRIMARY KEY,

    file_name VARCHAR(255) NOT NULL,

    file_size BIGINT NOT NULL
        CONSTRAINT physical_files_file_size_check CHECK (file_size > 0),

    file_hash VARCHAR(64) NOT NULL UNIQUE,

    storage_path TEXT,

    status VARCHAR(30) NOT NULL DEFAULT 'UPLOADING'
        CONSTRAINT physical_files_status_check
        CHECK (
            status IN (
                'UPLOADING',
                'UPLOADED',
                'PROCESSING',
                'AWAITING_RULES',
                'SUCCESS',
                'FAILED'
            )
        ),

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    rule_version INT NOT NULL DEFAULT 0,

    -- Legacy column, superseded by dataset_version_files. Not
    -- populated by current application code. Do not remove without
    -- a dedicated migration (see project brief, section 27).
    dataset_version_id BIGINT,

    CONSTRAINT fk_physical_files_dataset_version
        FOREIGN KEY (dataset_version_id)
        REFERENCES dataset_versions(dataset_version_id)
);

CREATE INDEX idx_physical_files_dataset_version_id
    ON physical_files(dataset_version_id);


-- =========================================================
-- 5. DATASET VERSION FILES
-- Junction table: one physical file may be linked to multiple
-- dataset versions (dedup-friendly many-to-many).
-- =========================================================

CREATE TABLE dataset_version_files (
    dataset_version_file_id BIGSERIAL PRIMARY KEY,

    dataset_version_id BIGINT NOT NULL,
    file_id BIGINT NOT NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT dataset_version_files_dataset_version_id_fkey
        FOREIGN KEY (dataset_version_id)
        REFERENCES dataset_versions(dataset_version_id),

    CONSTRAINT dataset_version_files_file_id_fkey
        FOREIGN KEY (file_id) REFERENCES physical_files(file_id),

    CONSTRAINT uq_dataset_version_file UNIQUE (dataset_version_id, file_id)
);

CREATE INDEX idx_dataset_version_files_dataset_version
    ON dataset_version_files(dataset_version_id);
CREATE INDEX idx_dataset_version_files_file
    ON dataset_version_files(file_id);


-- =========================================================
-- 6. DATASET RELATIONSHIPS
-- Candidate/approved relationships between datasets. Populated by
-- discovery logic; never auto-approved (see project brief, Phase 4).
-- =========================================================

CREATE TABLE dataset_relationships (
    relationship_id BIGSERIAL PRIMARY KEY,

    parent_dataset_id BIGINT NOT NULL,
    parent_column_name VARCHAR(255) NOT NULL,

    child_dataset_id BIGINT NOT NULL,
    child_column_name VARCHAR(255) NOT NULL,

    relationship_type VARCHAR(30) NOT NULL
        CONSTRAINT chk_relationship_type
        CHECK (
            relationship_type IN (
                'ONE_TO_ONE',
                'ONE_TO_MANY',
                'MANY_TO_ONE',
                'MANY_TO_MANY'
            )
        ),

    status VARCHAR(30) NOT NULL DEFAULT 'CANDIDATE'
        CONSTRAINT chk_relationship_status
        CHECK (status IN ('CANDIDATE', 'APPROVED', 'REJECTED')),

    confidence NUMERIC
        CONSTRAINT chk_relationship_confidence
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),

    discovery_method VARCHAR(100),
    evidence JSONB,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT dataset_relationships_parent_dataset_id_fkey
        FOREIGN KEY (parent_dataset_id) REFERENCES datasets(dataset_id),

    CONSTRAINT dataset_relationships_child_dataset_id_fkey
        FOREIGN KEY (child_dataset_id) REFERENCES datasets(dataset_id),

    CONSTRAINT chk_relationship_not_self
        CHECK (parent_dataset_id <> child_dataset_id),

    CONSTRAINT uq_dataset_relationship UNIQUE (
        parent_dataset_id,
        parent_column_name,
        child_dataset_id,
        child_column_name
    )
);

CREATE INDEX idx_relationship_parent ON dataset_relationships(parent_dataset_id);
CREATE INDEX idx_relationship_child ON dataset_relationships(child_dataset_id);


-- =========================================================
-- 7. UPLOAD REQUESTS
-- A user's logical request to upload a physical file into a
-- workspace/dataset. workspace_id/dataset_id are nullable because
-- historical uploads have no reliable logical context.
-- =========================================================

CREATE TABLE upload_requests (
    upload_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,
    file_id BIGINT NOT NULL,

    status VARCHAR(30) NOT NULL DEFAULT 'UPLOADING'
        CONSTRAINT upload_requests_status_check
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

    workspace_id BIGINT,
    dataset_id BIGINT,

    CONSTRAINT upload_requests_file_id_fkey
        FOREIGN KEY (file_id) REFERENCES physical_files(file_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_upload_requests_workspace
        FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),

    CONSTRAINT fk_upload_requests_dataset
        FOREIGN KEY (dataset_id) REFERENCES datasets(dataset_id)
);

CREATE INDEX idx_upload_requests_file_id ON upload_requests(file_id);
CREATE INDEX idx_upload_requests_user_id ON upload_requests(user_id);
CREATE INDEX idx_upload_requests_workspace_id ON upload_requests(workspace_id);
CREATE INDEX idx_upload_requests_dataset_id ON upload_requests(dataset_id);


-- =========================================================
-- 8. PROCESSING ATTEMPTS
-- Tracks every processing attempt and stage
-- (BRONZE / SILVER / GOLD).
-- =========================================================

CREATE TABLE processing_attempts (
    attempt_id BIGSERIAL PRIMARY KEY,

    file_id BIGINT NOT NULL,

    attempt_number INT NOT NULL
        CONSTRAINT processing_attempts_attempt_number_check
        CHECK (attempt_number > 0),

    stage VARCHAR(30) NOT NULL
        CONSTRAINT processing_attempts_stage_check
        CHECK (stage IN ('BRONZE', 'SILVER', 'GOLD')),

    status VARCHAR(30) NOT NULL
        CONSTRAINT processing_attempts_status_check
        CHECK (status IN ('PROCESSING', 'SUCCESS', 'FAILED', 'CRASHED')),

    error_message TEXT,

    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    CONSTRAINT processing_attempts_file_id_fkey
        FOREIGN KEY (file_id) REFERENCES physical_files(file_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_processing_attempts_file_id ON processing_attempts(file_id);
CREATE INDEX idx_processing_attempts_stage_status
    ON processing_attempts(stage, status);

-- A file may not have two active (PROCESSING) attempts at once.
-- Enforced at the database level, not just in application code.
CREATE UNIQUE INDEX ux_processing_attempts_active_file
    ON processing_attempts(file_id)
    WHERE status = 'PROCESSING';


-- =========================================================
-- 9. DATASET PROFILES
-- Column-level profiling information, keyed by physical file
-- (content-deterministic, so this stays file-scoped).
-- =========================================================

CREATE TABLE dataset_profiles (
    profile_id BIGSERIAL PRIMARY KEY,

    file_id BIGINT NOT NULL,

    column_name VARCHAR(255) NOT NULL,
    inferred_type VARCHAR(50),

    null_count BIGINT NOT NULL DEFAULT 0,
    distinct_count BIGINT,
    duplicate_count BIGINT,

    min_value TEXT,
    max_value TEXT,
    negative_count BIGINT,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT dataset_profiles_file_id_fkey
        FOREIGN KEY (file_id) REFERENCES physical_files(file_id),

    CONSTRAINT dataset_profiles_file_id_column_name_key
        UNIQUE (file_id, column_name)
);


-- =========================================================
-- 10. DATASET PROFILE SUMMARIES
-- Row/column counts per physical file.
-- =========================================================

CREATE TABLE dataset_profile_summaries (
    profile_summary_id BIGSERIAL PRIMARY KEY,

    file_id BIGINT NOT NULL UNIQUE,

    total_rows BIGINT NOT NULL,
    total_columns INT NOT NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT dataset_profile_summaries_file_id_fkey
        FOREIGN KEY (file_id) REFERENCES physical_files(file_id)
);


-- =========================================================
-- 11. BUSINESS RULES
-- User-approved, executable rule configuration.
-- Currently scoped by physical file_id (see project brief section 19
-- / the F.4 finding in the audit: this becomes dataset_version-scoped
-- in a later migration).
-- =========================================================

CREATE TABLE business_rules (
    rule_id BIGSERIAL PRIMARY KEY,

    file_id BIGINT NOT NULL,

    column_name VARCHAR(255),
    rule_type VARCHAR(50) NOT NULL,
    rule_config JSONB NOT NULL,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT business_rules_file_id_fkey
        FOREIGN KEY (file_id) REFERENCES physical_files(file_id),

    CONSTRAINT uq_business_rule_file_column_type
        UNIQUE (file_id, column_name, rule_type)
);


-- =========================================================
-- 12. BUSINESS RULE ANSWERS
-- The raw answers a user submitted (audit trail), independent of
-- whether they resulted in an active rule.
-- =========================================================

CREATE TABLE business_rule_answers (
    answer_id BIGSERIAL PRIMARY KEY,

    file_id BIGINT NOT NULL,

    column_name VARCHAR(255) NOT NULL,
    rule_type VARCHAR(50) NOT NULL,

    answer VARCHAR(30) NOT NULL
        CONSTRAINT chk_business_rule_answer
        CHECK (
            answer IN (
                'YES', 'NO', 'NOT_SURE',
                'ALLOW', 'KEEP_FIRST', 'KEEP_LATEST', 'QUARANTINE',
                'DECIMAL', 'DATETIME'
            )
        ),

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT business_rule_answers_file_id_fkey
        FOREIGN KEY (file_id) REFERENCES physical_files(file_id),

    CONSTRAINT uq_business_rule_answer
        UNIQUE (file_id, column_name, rule_type)
);


-- =========================================================
-- 13. DATA QUALITY RUNS
-- One row per Silver execution attempt, tied to the
-- processing_attempts row and the rule_version enforced.
-- =========================================================

CREATE TABLE data_quality_runs (
    dq_run_id BIGSERIAL PRIMARY KEY,

    file_id BIGINT NOT NULL,

    total_rows BIGINT NOT NULL,
    valid_rows BIGINT NOT NULL,
    rejected_rows BIGINT NOT NULL,

    silver_path TEXT,
    quarantine_path TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    attempt_id BIGINT,
    rule_version INT,

    CONSTRAINT data_quality_runs_file_id_fkey
        FOREIGN KEY (file_id) REFERENCES physical_files(file_id),

    CONSTRAINT data_quality_runs_attempt_id_fkey
        FOREIGN KEY (attempt_id) REFERENCES processing_attempts(attempt_id),

    CONSTRAINT uq_data_quality_run_attempt UNIQUE (attempt_id)
);


-- =========================================================
-- 14. DATA QUALITY ISSUES
-- Aggregated violation counts per DQ run/column/rule.
-- =========================================================

CREATE TABLE data_quality_issues (
    dq_issue_id BIGSERIAL PRIMARY KEY,

    dq_run_id BIGINT NOT NULL,

    column_name VARCHAR(255) NOT NULL,
    rule_type VARCHAR(50) NOT NULL,
    violation_count BIGINT NOT NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT data_quality_issues_dq_run_id_fkey
        FOREIGN KEY (dq_run_id) REFERENCES data_quality_runs(dq_run_id)
        ON DELETE CASCADE,

    CONSTRAINT uq_dq_issue_run_column_rule
        UNIQUE (dq_run_id, column_name, rule_type)
);


-- =========================================================
-- 15. GOLD RUNS
-- One row per successful Gold publication, with lineage back to
-- the exact DQ run/processing attempt it was built from.
-- =========================================================

CREATE TABLE gold_runs (
    gold_run_id BIGSERIAL PRIMARY KEY,

    file_id BIGINT NOT NULL,
    attempt_id BIGINT NOT NULL,
    source_dq_run_id BIGINT NOT NULL,

    gold_type VARCHAR(30) NOT NULL,
    row_count BIGINT NOT NULL,
    gold_path TEXT NOT NULL,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT gold_runs_file_id_fkey
        FOREIGN KEY (file_id) REFERENCES physical_files(file_id),

    CONSTRAINT gold_runs_attempt_id_fkey
        FOREIGN KEY (attempt_id) REFERENCES processing_attempts(attempt_id),

    CONSTRAINT gold_runs_source_dq_run_id_fkey
        FOREIGN KEY (source_dq_run_id) REFERENCES data_quality_runs(dq_run_id),

    CONSTRAINT uq_gold_run_attempt UNIQUE (attempt_id)
);
