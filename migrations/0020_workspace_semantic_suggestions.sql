-- Separate review-only workspace history; no backfill or changes to dataset state.
CREATE TABLE workspace_semantic_suggestions (
    suggestion_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
    owner_key TEXT NOT NULL,
    suggestion_version INTEGER NOT NULL CHECK(suggestion_version>0),
    algorithm_version INTEGER NOT NULL CHECK(algorithm_version>0),
    source_signature TEXT NOT NULL CHECK(length(source_signature)=64),
    configuration_key TEXT NOT NULL CHECK(length(configuration_key)=64),
    source_pins JSONB NOT NULL CHECK(jsonb_typeof(source_pins)='array'),
    coverage JSONB NOT NULL CHECK(jsonb_typeof(coverage)='object'),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    resolved_model TEXT,
    status TEXT NOT NULL CHECK(status IN ('GENERATING','READY','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    failure_code TEXT CHECK(failure_code IN ('TIMEOUT','RATE_LIMIT','MODEL_UNAVAILABLE','PROVIDER_UNAVAILABLE',
        'REFUSED','PROVIDER_ERROR','INVALID_OUTPUT','GENERATION_FAILED','INTERRUPTED')),
    compact_mode BOOLEAN NOT NULL,
    input_bytes INTEGER NOT NULL CHECK(input_bytes>0 AND input_bytes<=100000),
    column_count INTEGER NOT NULL CHECK(column_count>=0 AND column_count<=1000),
    reasoning JSONB CHECK(jsonb_typeof(reasoning)='object'),
    UNIQUE(workspace_id,source_signature,algorithm_version,configuration_key),
    UNIQUE(workspace_id,suggestion_version),
    CHECK(status<>'READY' OR (reasoning IS NOT NULL AND completed_at IS NOT NULL AND failure_code IS NULL))
);
CREATE FUNCTION protect_workspace_semantic_suggestion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE pin JSONB;
BEGIN
    IF TG_OP<>'DELETE' AND (TG_OP='INSERT' OR NEW.status='READY') THEN
        IF jsonb_array_length(NEW.source_pins)=0 OR jsonb_array_length(NEW.source_pins)<>(NEW.coverage->>'datasets_analyzed')::integer
            THEN RAISE EXCEPTION 'Workspace suggestion coverage mismatch'; END IF;
        FOR pin IN SELECT value FROM jsonb_array_elements(NEW.source_pins) LOOP
            IF NOT EXISTS(SELECT 1 FROM datasets d
                JOIN semantic_profiles p ON p.profile_id=d.current_profile_id
                JOIN semantic_suggestions s ON s.suggestion_id=(pin->>'suggestion_id')::bigint
                JOIN dataset_state_versions st ON st.state_id=d.current_state_id
                WHERE d.dataset_id=(pin->>'dataset_id')::bigint AND d.workspace_id=NEW.workspace_id
                AND d.owner=NEW.owner_key AND d.status<>'ARCHIVED' AND d.profile_status='READY'
                AND p.status='READY' AND p.profile_id=(pin->>'profile_id')::bigint
                AND p.profile_version=(pin->>'profile_version')::integer
                AND p.source_state_id=st.state_id AND st.state_id=(pin->>'state_id')::bigint
                AND st.state_version=(pin->>'state_version')::integer
                AND st.dataset_version_id=(pin->>'dataset_version_id')::bigint
                AND s.dataset_id=d.dataset_id AND s.source_profile_id=p.profile_id AND s.status='READY'
                AND s.suggestion_version=(pin->>'suggestion_version')::integer)
                THEN RAISE EXCEPTION 'Workspace suggestion source mismatch'; END IF;
        END LOOP;
    END IF;
    IF TG_OP='INSERT' THEN
        IF NOT EXISTS(SELECT 1 FROM workspaces WHERE workspace_id=NEW.workspace_id AND owner=NEW.owner_key)
            THEN RAISE EXCEPTION 'Workspace suggestion owner mismatch'; END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' OR OLD.status='READY' THEN RAISE EXCEPTION 'Workspace suggestion history is immutable'; END IF;
    IF (to_jsonb(NEW)-ARRAY['status','completed_at','failure_code','reasoning','resolved_model']) IS DISTINCT FROM
       (to_jsonb(OLD)-ARRAY['status','completed_at','failure_code','reasoning','resolved_model'])
        THEN RAISE EXCEPTION 'Workspace suggestion pins are immutable'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER workspace_semantic_history BEFORE INSERT OR UPDATE OR DELETE ON workspace_semantic_suggestions
    FOR EACH ROW EXECUTE FUNCTION protect_workspace_semantic_suggestion();
