-- Independent deterministic evidence; existing state/Gold histories are unchanged.
CREATE TABLE semantic_profiles (
    profile_id BIGSERIAL PRIMARY KEY,
    owner_key TEXT NOT NULL,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
    dataset_id BIGINT NOT NULL REFERENCES datasets(dataset_id),
    source_state_id BIGINT NOT NULL REFERENCES dataset_state_versions(state_id),
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_versions(dataset_version_id),
    policy_id BIGINT NOT NULL REFERENCES dataset_load_policies(policy_id),
    source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
    profile_version INTEGER NOT NULL CHECK(profile_version>0),
    algorithm_version INTEGER NOT NULL CHECK(algorithm_version>0),
    status TEXT NOT NULL CHECK(status IN ('PROFILING','READY','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    failure_code TEXT CHECK(failure_code IN ('PROFILE_FAILED','INTERRUPTED')),
    summary JSONB CHECK(jsonb_typeof(summary)='object'),
    UNIQUE(dataset_id,source_state_id,algorithm_version),
    UNIQUE(dataset_id,profile_version),
    CHECK(status<>'READY' OR (summary IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE INDEX semantic_profiles_workspace_ready ON semantic_profiles(workspace_id,dataset_id,source_state_id) WHERE status='READY';
CREATE TABLE semantic_column_evidence (
    profile_id BIGINT NOT NULL REFERENCES semantic_profiles(profile_id),
    ordinal_position INTEGER NOT NULL CHECK(ordinal_position>0),
    column_name TEXT NOT NULL,
    evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence)='object'),
    PRIMARY KEY(profile_id,ordinal_position),
    UNIQUE(profile_id,column_name)
);
ALTER TABLE datasets ADD COLUMN current_profile_id BIGINT REFERENCES semantic_profiles(profile_id);
ALTER TABLE datasets ADD COLUMN profile_status TEXT NOT NULL DEFAULT 'NOT_PROFILED'
    CHECK(profile_status IN ('NOT_PROFILED','STALE','PROFILING','READY','FAILED'));
CREATE FUNCTION protect_semantic_profile() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR OLD.status='READY' THEN RAISE EXCEPTION 'Profile history is immutable'; END IF;
    IF (NEW.owner_key,NEW.workspace_id,NEW.dataset_id,NEW.source_state_id,NEW.dataset_version_id,NEW.policy_id,NEW.source_sha256,NEW.profile_version,NEW.algorithm_version,NEW.created_at)
        IS DISTINCT FROM (OLD.owner_key,OLD.workspace_id,OLD.dataset_id,OLD.source_state_id,OLD.dataset_version_id,OLD.policy_id,OLD.source_sha256,OLD.profile_version,OLD.algorithm_version,OLD.created_at)
        THEN RAISE EXCEPTION 'Profile pins are immutable'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER semantic_profile_history BEFORE UPDATE OR DELETE ON semantic_profiles FOR EACH ROW EXECUTE FUNCTION protect_semantic_profile();
CREATE FUNCTION validate_semantic_profile_pins() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM dataset_state_versions s JOIN datasets d ON d.dataset_id=s.dataset_id
        JOIN workspaces w ON w.workspace_id=d.workspace_id
        WHERE s.state_id=NEW.source_state_id AND s.dataset_id=NEW.dataset_id AND s.status='PUBLISHED'
        AND s.dataset_version_id=NEW.dataset_version_id AND s.policy_id=NEW.policy_id AND s.manifest_sha256=NEW.source_sha256
        AND d.workspace_id=NEW.workspace_id AND d.owner=NEW.owner_key AND w.owner=NEW.owner_key)
        THEN RAISE EXCEPTION 'Profile requires owned published state'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER semantic_profile_pins BEFORE INSERT ON semantic_profiles FOR EACH ROW EXECUTE FUNCTION validate_semantic_profile_pins();
CREATE FUNCTION protect_column_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target BIGINT;
BEGIN
    target:=CASE WHEN TG_OP='DELETE' THEN OLD.profile_id ELSE NEW.profile_id END;
    IF EXISTS(SELECT 1 FROM semantic_profiles WHERE profile_id=target AND status='READY')
        OR (TG_OP='UPDATE' AND NEW.profile_id<>OLD.profile_id) THEN RAISE EXCEPTION 'Published column evidence is immutable'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER semantic_column_history BEFORE INSERT OR UPDATE OR DELETE ON semantic_column_evidence FOR EACH ROW EXECUTE FUNCTION protect_column_evidence();
CREATE FUNCTION validate_semantic_profile_head() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.current_state_id IS DISTINCT FROM OLD.current_state_id THEN NEW.profile_status:='STALE'; END IF;
    IF NEW.current_profile_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM semantic_profiles p
        WHERE p.profile_id=NEW.current_profile_id AND p.dataset_id=NEW.dataset_id AND p.workspace_id=NEW.workspace_id
        AND p.owner_key=NEW.owner AND p.status='READY'
        AND (NEW.profile_status<>'READY' OR p.source_state_id=NEW.current_state_id)) THEN RAISE EXCEPTION 'Profile pointer must reference owned successful evidence'; END IF;
    IF NEW.profile_status='READY' AND NEW.current_profile_id IS NULL THEN RAISE EXCEPTION 'Ready profile requires evidence'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER semantic_profile_head BEFORE UPDATE ON datasets FOR EACH ROW EXECUTE FUNCTION validate_semantic_profile_head();
