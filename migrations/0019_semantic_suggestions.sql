-- Suggestions are separate from evidence and future trusted approvals. No backfill.
CREATE TABLE semantic_suggestions (
    suggestion_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
    dataset_id BIGINT NOT NULL REFERENCES datasets(dataset_id),
    owner_key TEXT NOT NULL,
    source_profile_id BIGINT NOT NULL REFERENCES semantic_profiles(profile_id),
    source_state_id BIGINT NOT NULL REFERENCES dataset_state_versions(state_id),
    source_state_version INTEGER NOT NULL CHECK(source_state_version>0),
    suggestion_version INTEGER NOT NULL CHECK(suggestion_version>0),
    semantic_model_version INTEGER NOT NULL CHECK(semantic_model_version>0),
    configuration_key TEXT NOT NULL CHECK(length(configuration_key)=64),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    resolved_model TEXT,
    status TEXT NOT NULL CHECK(status IN ('GENERATING','READY','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    failure_code TEXT CHECK(failure_code IN ('TIMEOUT','RATE_LIMIT','MODEL_UNAVAILABLE','PROVIDER_UNAVAILABLE',
        'REFUSED','PROVIDER_ERROR','INVALID_OUTPUT','GENERATION_FAILED','INTERRUPTED')),
    compact_mode BOOLEAN NOT NULL,
    input_bytes INTEGER NOT NULL CHECK(input_bytes>0 AND input_bytes<=60000),
    reasoning JSONB CHECK(jsonb_typeof(reasoning)='object'),
    UNIQUE(dataset_id,source_profile_id,semantic_model_version,configuration_key),
    UNIQUE(dataset_id,suggestion_version),
    CHECK(status<>'READY' OR (reasoning IS NOT NULL AND completed_at IS NOT NULL AND failure_code IS NULL))
);
CREATE INDEX semantic_suggestions_workspace_ready ON semantic_suggestions(workspace_id,dataset_id,source_profile_id) WHERE status='READY';
CREATE FUNCTION protect_semantic_suggestion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR OLD.status='READY' THEN RAISE EXCEPTION 'Suggestion history is immutable'; END IF;
    IF (NEW.workspace_id,NEW.dataset_id,NEW.owner_key,NEW.source_profile_id,NEW.source_state_id,NEW.source_state_version,
        NEW.suggestion_version,NEW.semantic_model_version,NEW.configuration_key,NEW.provider,NEW.model,NEW.created_at,NEW.compact_mode,NEW.input_bytes)
        IS DISTINCT FROM
        (OLD.workspace_id,OLD.dataset_id,OLD.owner_key,OLD.source_profile_id,OLD.source_state_id,OLD.source_state_version,
        OLD.suggestion_version,OLD.semantic_model_version,OLD.configuration_key,OLD.provider,OLD.model,OLD.created_at,OLD.compact_mode,OLD.input_bytes)
        THEN RAISE EXCEPTION 'Suggestion pins are immutable'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER semantic_suggestion_history BEFORE UPDATE OR DELETE ON semantic_suggestions FOR EACH ROW EXECUTE FUNCTION protect_semantic_suggestion();
CREATE FUNCTION validate_semantic_suggestion_pins() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM semantic_profiles p JOIN datasets d ON d.dataset_id=p.dataset_id
        JOIN workspaces w ON w.workspace_id=d.workspace_id JOIN dataset_state_versions s ON s.state_id=p.source_state_id
        WHERE p.profile_id=NEW.source_profile_id AND p.status='READY' AND p.dataset_id=NEW.dataset_id
        AND p.workspace_id=NEW.workspace_id AND p.owner_key=NEW.owner_key AND d.owner=NEW.owner_key AND w.owner=NEW.owner_key
        AND s.state_id=NEW.source_state_id AND s.state_version=NEW.source_state_version
        AND d.current_profile_id=p.profile_id AND d.current_state_id=p.source_state_id AND d.profile_status='READY')
        THEN RAISE EXCEPTION 'Suggestion requires owned current ready profile'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER semantic_suggestion_pins BEFORE INSERT ON semantic_suggestions FOR EACH ROW EXECUTE FUNCTION validate_semantic_suggestion_pins();
