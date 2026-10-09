-- Cumulative analytics is independent of delivery Gold and trusted-state publication.
ALTER TABLE datasets DROP CONSTRAINT datasets_state_analytics_status_check;
ALTER TABLE datasets ADD CONSTRAINT datasets_state_analytics_status_check
    CHECK (state_analytics_status IN ('STALE','REFRESHING','FRESH','FAILED'));
CREATE TABLE dataset_gold_runs (
    gold_run_id BIGSERIAL PRIMARY KEY,
    dataset_id BIGINT NOT NULL REFERENCES datasets(dataset_id),
    source_state_id BIGINT NOT NULL REFERENCES dataset_state_versions(state_id),
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_versions(dataset_version_id),
    policy_id BIGINT NOT NULL REFERENCES dataset_load_policies(policy_id),
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256)=64),
    build_version INTEGER NOT NULL DEFAULT 1 CHECK (build_version=1),
    status TEXT NOT NULL CHECK (status IN ('REFRESHING','SUCCESS','FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    failure_code TEXT CHECK (failure_code IN ('BUILD_FAILED','VALIDATION_FAILED','INTERRUPTED')),
    manifest_key TEXT,
    manifest_sha256 TEXT,
    catalog JSONB,
    row_count BIGINT CHECK (row_count >= 0),
    UNIQUE(dataset_id,source_state_id,build_version),
    CHECK (status <> 'SUCCESS' OR (completed_at IS NOT NULL AND manifest_key IS NOT NULL
        AND length(manifest_sha256)=64 AND catalog IS NOT NULL AND row_count IS NOT NULL))
);
ALTER TABLE datasets ADD COLUMN current_gold_run_id BIGINT REFERENCES dataset_gold_runs(gold_run_id);
CREATE FUNCTION protect_dataset_gold_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR OLD.status='SUCCESS' THEN
        RAISE EXCEPTION 'Published cumulative Gold history is immutable';
    END IF;
    IF (NEW.dataset_id,NEW.source_state_id,NEW.dataset_version_id,NEW.policy_id,NEW.source_sha256,NEW.build_version)
        IS DISTINCT FROM (OLD.dataset_id,OLD.source_state_id,OLD.dataset_version_id,OLD.policy_id,OLD.source_sha256,OLD.build_version) THEN
        RAISE EXCEPTION 'Cumulative Gold source pins are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_dataset_gold_run BEFORE UPDATE OR DELETE ON dataset_gold_runs
    FOR EACH ROW EXECUTE FUNCTION protect_dataset_gold_run();
CREATE FUNCTION validate_dataset_gold_pins() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM dataset_state_versions s WHERE s.state_id=NEW.source_state_id
        AND s.dataset_id=NEW.dataset_id AND s.dataset_version_id=NEW.dataset_version_id
        AND s.policy_id=NEW.policy_id AND s.manifest_sha256=NEW.source_sha256 AND s.status='PUBLISHED') THEN
        RAISE EXCEPTION 'Gold requires an owned published trusted state';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER dataset_gold_source_pins BEFORE INSERT ON dataset_gold_runs
    FOR EACH ROW EXECUTE FUNCTION validate_dataset_gold_pins();
CREATE FUNCTION validate_dataset_gold_head() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.current_state_id IS DISTINCT FROM OLD.current_state_id THEN
        NEW.state_analytics_status := 'STALE';
    END IF;
    IF NEW.current_gold_run_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM dataset_gold_runs g WHERE g.gold_run_id=NEW.current_gold_run_id
        AND g.dataset_id=NEW.dataset_id AND g.status='SUCCESS'
        AND (NEW.state_analytics_status <> 'FRESH' OR g.source_state_id=NEW.current_state_id)) THEN
        RAISE EXCEPTION 'Gold pointer/freshness must reference owned successful state';
    END IF;
    IF NEW.state_analytics_status='FRESH' AND NEW.current_gold_run_id IS NULL THEN
        RAISE EXCEPTION 'Fresh analytics requires published Gold';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER dataset_gold_head_integrity BEFORE UPDATE ON datasets
    FOR EACH ROW EXECUTE FUNCTION validate_dataset_gold_head();
