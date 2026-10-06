-- Metadata foundations only. Existing successful artifacts/statuses are unchanged.
ALTER TABLE dataset_versions ADD CONSTRAINT incremental_version_scope UNIQUE(dataset_id, dataset_version_id);
ALTER TABLE upload_requests ADD CONSTRAINT incremental_upload_scope UNIQUE(dataset_id, upload_id);

CREATE TABLE dataset_load_policies (
    policy_id BIGSERIAL PRIMARY KEY,
    dataset_id BIGINT NOT NULL,
    dataset_version_id BIGINT NOT NULL,
    policy_version INTEGER NOT NULL CHECK(policy_version > 0),
    load_strategy TEXT NOT NULL CHECK(load_strategy IN ('APPEND','UPSERT','SNAPSHOT')),
    business_keys JSONB NOT NULL CHECK(jsonb_typeof(business_keys)='array'),
    schema_columns JSONB NOT NULL CHECK(jsonb_typeof(schema_columns)='array'),
    schema_hash VARCHAR(64) NOT NULL,
    schema_evolution_policy TEXT NOT NULL CHECK(schema_evolution_policy IN ('STRICT','ALLOW_ADDITIVE')),
    key_conflict_policy TEXT NOT NULL DEFAULT 'REJECT' CHECK(key_conflict_policy='REJECT'),
    missing_record_policy TEXT NOT NULL DEFAULT 'MARK_INACTIVE' CHECK(missing_record_policy='MARK_INACTIVE'),
    event_time_column TEXT,
    normalization_version INTEGER NOT NULL DEFAULT 1 CHECK(normalization_version=1),
    created_by BIGINT NOT NULL REFERENCES users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(dataset_id,policy_version),
    UNIQUE(policy_id,dataset_id,dataset_version_id),
    FOREIGN KEY(dataset_id,dataset_version_id) REFERENCES dataset_versions(dataset_id,dataset_version_id),
    CHECK(load_strategy='APPEND' OR jsonb_array_length(business_keys)>0)
);

CREATE TABLE delivery_applications (
    application_id BIGSERIAL PRIMARY KEY,
    upload_request_id BIGINT NOT NULL UNIQUE,
    dataset_id BIGINT NOT NULL,
    dataset_version_id BIGINT NOT NULL,
    dataset_version_file_id BIGINT NOT NULL REFERENCES dataset_version_files(dataset_version_file_id),
    policy_id BIGINT NOT NULL,
    applied_rule_version INTEGER NOT NULL,
    source_dq_run_id BIGINT NOT NULL REFERENCES data_quality_runs(dq_run_id),
    status TEXT NOT NULL DEFAULT 'PREPARED' CHECK(status IN ('PREPARED','RUNNING','SUCCESS','FAILED')),
    ingestion_time TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    input_rows BIGINT CHECK(input_rows >= 0),
    valid_rows BIGINT CHECK(valid_rows >= 0),
    rejected_rows BIGINT CHECK(rejected_rows >= 0),
    inserted_rows BIGINT CHECK(inserted_rows >= 0),
    updated_rows BIGINT CHECK(updated_rows >= 0),
    unchanged_rows BIGINT CHECK(unchanged_rows >= 0),
    duplicate_rows BIGINT CHECK(duplicate_rows >= 0),
    deactivated_rows BIGINT CHECK(deactivated_rows >= 0),
    current_state_rows BIGINT CHECK(current_state_rows >= 0),
    failure_code TEXT CHECK(failure_code IN ('APPLICATION_FAILED','STATE_CONFLICT','CANDIDATE_INVALID')),
    result_state_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(application_id,dataset_id,dataset_version_id,policy_id),
    FOREIGN KEY(dataset_id,upload_request_id) REFERENCES upload_requests(dataset_id,upload_id),
    FOREIGN KEY(policy_id,dataset_id,dataset_version_id) REFERENCES dataset_load_policies(policy_id,dataset_id,dataset_version_id),
    FOREIGN KEY(dataset_version_id,applied_rule_version) REFERENCES approved_rule_policies(dataset_version_id,rule_version),
    CHECK(status <> 'SUCCESS' OR (result_state_id IS NOT NULL AND completed_at IS NOT NULL)),
    CHECK(status <> 'FAILED' OR failure_code IS NOT NULL)
);

CREATE TABLE dataset_state_versions (
    state_id BIGSERIAL PRIMARY KEY,
    dataset_id BIGINT NOT NULL,
    dataset_version_id BIGINT NOT NULL,
    policy_id BIGINT NOT NULL,
    source_application_id BIGINT NOT NULL,
    previous_state_id BIGINT,
    state_version BIGINT NOT NULL CHECK(state_version > 0),
    manifest_key TEXT NOT NULL UNIQUE,
    manifest_sha256 VARCHAR(64) NOT NULL CHECK(manifest_sha256 ~ '^[a-f0-9]{64}$'),
    row_count BIGINT CHECK(row_count >= 0),
    status TEXT NOT NULL DEFAULT 'STAGED' CHECK(status IN ('STAGED','VALIDATED','PUBLISHED')),
    validated_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(dataset_id,state_version),
    UNIQUE(dataset_id,state_id),
    UNIQUE(state_id,source_application_id),
    FOREIGN KEY(source_application_id,dataset_id,dataset_version_id,policy_id)
        REFERENCES delivery_applications(application_id,dataset_id,dataset_version_id,policy_id),
    FOREIGN KEY(dataset_id,previous_state_id) REFERENCES dataset_state_versions(dataset_id,state_id),
    CHECK(status='STAGED' OR (validated_at IS NOT NULL AND row_count IS NOT NULL)),
    CHECK(status <> 'PUBLISHED' OR published_at IS NOT NULL)
);

ALTER TABLE delivery_applications ADD CONSTRAINT application_result_lineage
    FOREIGN KEY(result_state_id,application_id) REFERENCES dataset_state_versions(state_id,source_application_id);
ALTER TABLE datasets ADD COLUMN current_state_id BIGINT;
ALTER TABLE datasets ADD CONSTRAINT dataset_state_ownership
    FOREIGN KEY(dataset_id,current_state_id) REFERENCES dataset_state_versions(dataset_id,state_id);

CREATE FUNCTION protect_incremental_history() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Incremental history cannot be deleted'; END IF;
    IF TG_TABLE_NAME='dataset_load_policies' THEN RAISE EXCEPTION 'Load policies are immutable'; END IF;
    IF TG_TABLE_NAME='delivery_applications' THEN
        IF OLD.status='SUCCESS' OR
           ROW(NEW.upload_request_id,NEW.dataset_id,NEW.dataset_version_id,NEW.dataset_version_file_id,NEW.policy_id,NEW.applied_rule_version,NEW.source_dq_run_id,NEW.ingestion_time)
           IS DISTINCT FROM ROW(OLD.upload_request_id,OLD.dataset_id,OLD.dataset_version_id,OLD.dataset_version_file_id,OLD.policy_id,OLD.applied_rule_version,OLD.source_dq_run_id,OLD.ingestion_time)
        THEN RAISE EXCEPTION 'Application lineage or successful result is immutable'; END IF;
        IF NEW.status='SUCCESS' AND NOT EXISTS(SELECT 1 FROM dataset_state_versions
            WHERE state_id=NEW.result_state_id AND source_application_id=NEW.application_id AND status='PUBLISHED')
        THEN RAISE EXCEPTION 'Application success requires its published state'; END IF;
    ELSIF TG_TABLE_NAME='dataset_state_versions' THEN
        IF NEW.status='PUBLISHED' AND OLD.status <> 'VALIDATED'
        THEN RAISE EXCEPTION 'Only validated candidates can be published'; END IF;
        IF OLD.status='PUBLISHED' OR ROW(NEW.dataset_id,NEW.dataset_version_id,NEW.policy_id,NEW.source_application_id,NEW.previous_state_id,NEW.state_version,NEW.manifest_key,NEW.manifest_sha256)
           IS DISTINCT FROM ROW(OLD.dataset_id,OLD.dataset_version_id,OLD.policy_id,OLD.source_application_id,OLD.previous_state_id,OLD.state_version,OLD.manifest_key,OLD.manifest_sha256)
        THEN RAISE EXCEPTION 'Published state or candidate lineage is immutable'; END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_load_policy BEFORE UPDATE OR DELETE ON dataset_load_policies FOR EACH ROW EXECUTE FUNCTION protect_incremental_history();
CREATE TRIGGER immutable_application_lineage BEFORE UPDATE OR DELETE ON delivery_applications FOR EACH ROW EXECUTE FUNCTION protect_incremental_history();
CREATE TRIGGER immutable_state_lineage BEFORE UPDATE OR DELETE ON dataset_state_versions FOR EACH ROW EXECUTE FUNCTION protect_incremental_history();

CREATE FUNCTION require_application_context() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM upload_requests u
        JOIN dataset_version_files a ON a.file_id=u.file_id AND a.dataset_version_file_id=NEW.dataset_version_file_id
        JOIN data_quality_runs dq ON dq.dq_run_id=NEW.source_dq_run_id AND dq.dataset_version_file_id=a.dataset_version_file_id
        JOIN processing_attempts pa ON pa.attempt_id=dq.attempt_id AND pa.status='SUCCESS'
        WHERE u.upload_id=NEW.upload_request_id AND u.dataset_id=NEW.dataset_id AND u.archived_at IS NULL
          AND u.status='UPLOADED' AND a.dataset_version_id=NEW.dataset_version_id AND dq.rule_version=NEW.applied_rule_version)
    THEN RAISE EXCEPTION 'Application requires active delivery and matching successful Silver lineage'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER validated_application_context BEFORE INSERT ON delivery_applications FOR EACH ROW EXECUTE FUNCTION require_application_context();

CREATE FUNCTION require_published_dataset_head() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.current_state_id IS DISTINCT FROM OLD.current_state_id THEN
        IF NEW.current_state_id IS NULL OR NOT EXISTS(SELECT 1 FROM dataset_state_versions s
            WHERE s.state_id=NEW.current_state_id AND s.dataset_id=NEW.dataset_id AND s.status='PUBLISHED'
            AND s.previous_state_id IS NOT DISTINCT FROM OLD.current_state_id)
        THEN RAISE EXCEPTION 'Dataset head requires a published owned state'; END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER published_dataset_head BEFORE UPDATE OF current_state_id ON datasets FOR EACH ROW EXECUTE FUNCTION require_published_dataset_head();
