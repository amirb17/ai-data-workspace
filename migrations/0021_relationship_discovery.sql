-- Additive review-only metadata. No backfill, relationship execution or source-data mutation.
CREATE TABLE relationship_discovery_runs (
    run_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
    owner_key TEXT NOT NULL,
    run_version INTEGER NOT NULL CHECK(run_version>0),
    algorithm_version INTEGER NOT NULL CHECK(algorithm_version>0),
    source_signature TEXT NOT NULL CHECK(length(source_signature)=64),
    source_pins JSONB NOT NULL CHECK(jsonb_typeof(source_pins)='array'),
    coverage JSONB NOT NULL CHECK(jsonb_typeof(coverage)='object'),
    semantic_context JSONB NOT NULL CHECK(jsonb_typeof(semantic_context)='object'),
    status TEXT NOT NULL CHECK(status IN ('DISCOVERING','READY','FAILED')),
    failure_code TEXT CHECK(failure_code IN ('DISCOVERY_FAILED','SOURCE_CHANGED','INTERRUPTED','LIMIT_EXCEEDED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    candidate_count INTEGER CHECK(candidate_count>=0 AND candidate_count<=200),
    suppressed_composite_pairs INTEGER CHECK(suppressed_composite_pairs>=0),
    UNIQUE(workspace_id,run_id),
    UNIQUE(workspace_id,run_version),
    UNIQUE(workspace_id,source_signature,algorithm_version),
    CHECK(status<>'READY' OR (completed_at IS NOT NULL AND failure_code IS NULL AND candidate_count IS NOT NULL))
);
CREATE TABLE relationship_candidates (
    candidate_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    run_id BIGINT NOT NULL,
    candidate_key TEXT NOT NULL CHECK(length(candidate_key)=64),
    evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence)='object'),
    FOREIGN KEY(workspace_id,run_id) REFERENCES relationship_discovery_runs(workspace_id,run_id),
    UNIQUE(run_id,candidate_key),
    UNIQUE(workspace_id,candidate_id)
);
CREATE TABLE relationship_reviews (
    review_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    candidate_id BIGINT NOT NULL,
    candidate_key TEXT NOT NULL CHECK(length(candidate_key)=64),
    relationship_version INTEGER NOT NULL CHECK(relationship_version>0),
    status TEXT NOT NULL CHECK(status IN ('CONFIRMED','REJECTED')),
    reviewed_by BIGINT NOT NULL REFERENCES users(user_id),
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY(workspace_id,candidate_id) REFERENCES relationship_candidates(workspace_id,candidate_id),
    UNIQUE(workspace_id,candidate_key,relationship_version)
);
CREATE FUNCTION protect_relationship_metadata() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE pin JSONB; r relationship_discovery_runs; candidate relationship_candidates;
BEGIN
    IF TG_TABLE_NAME='relationship_discovery_runs' THEN
        IF TG_OP='DELETE' OR (TG_OP='UPDATE' AND OLD.status='READY') THEN
            RAISE EXCEPTION 'Relationship evidence history is immutable';
        END IF;
        IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['status','failure_code','completed_at','candidate_count','suppressed_composite_pairs']) IS DISTINCT FROM
            (to_jsonb(OLD)-ARRAY['status','failure_code','completed_at','candidate_count','suppressed_composite_pairs']) THEN
            RAISE EXCEPTION 'Relationship source pins are immutable';
        END IF;
        IF TG_OP='INSERT' OR NEW.status='READY' THEN
            IF NOT EXISTS(SELECT 1 FROM workspaces WHERE workspace_id=NEW.workspace_id AND owner=NEW.owner_key)
                THEN RAISE EXCEPTION 'Relationship workspace ownership mismatch'; END IF;
            IF jsonb_array_length(NEW.source_pins)<2 OR jsonb_array_length(NEW.source_pins)<>(NEW.coverage->>'datasets_analyzed')::integer
                THEN RAISE EXCEPTION 'Relationship coverage mismatch'; END IF;
            FOR pin IN SELECT value FROM jsonb_array_elements(NEW.source_pins) LOOP
                IF NOT EXISTS(SELECT 1 FROM datasets d JOIN semantic_profiles p ON p.profile_id=d.current_profile_id
                    JOIN dataset_state_versions s ON s.state_id=d.current_state_id
                    WHERE d.dataset_id=(pin->>'dataset_id')::bigint AND d.workspace_id=NEW.workspace_id AND d.owner=NEW.owner_key
                    AND d.status<>'ARCHIVED' AND d.profile_status='READY' AND p.status='READY'
                    AND p.dataset_id=d.dataset_id AND s.dataset_id=d.dataset_id AND s.status='PUBLISHED'
                    AND p.source_state_id=s.state_id AND p.profile_id=(pin->>'profile_id')::bigint
                    AND s.state_id=(pin->>'state_id')::bigint AND s.policy_id=(pin->>'policy_id')::bigint)
                    THEN RAISE EXCEPTION 'Relationship source mismatch'; END IF;
            END LOOP;
            IF NEW.status='READY' AND NEW.candidate_count<>(SELECT count(*) FROM relationship_candidates WHERE run_id=NEW.run_id)
                THEN RAISE EXCEPTION 'Relationship candidate count mismatch'; END IF;
        END IF;
    ELSE
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Relationship candidate/review history is immutable'; END IF;
        IF TG_TABLE_NAME='relationship_candidates' THEN
            SELECT * INTO r FROM relationship_discovery_runs WHERE run_id=NEW.run_id AND workspace_id=NEW.workspace_id;
            IF r.status IS DISTINCT FROM 'DISCOVERING' OR NEW.evidence->>'candidate_key'<>NEW.candidate_key
                OR (NEW.evidence->>'workspace_id')::bigint<>NEW.workspace_id THEN
                RAISE EXCEPTION 'Invalid relationship evidence context'; END IF;
        ELSE
            SELECT * INTO candidate FROM relationship_candidates WHERE candidate_id=NEW.candidate_id AND workspace_id=NEW.workspace_id;
            SELECT * INTO r FROM relationship_discovery_runs WHERE run_id=candidate.run_id;
            IF r.status IS DISTINCT FROM 'READY' OR candidate.candidate_key IS DISTINCT FROM NEW.candidate_key
                OR NOT EXISTS(SELECT 1 FROM users u JOIN workspaces w ON w.owner=u.owner_key
                    WHERE u.user_id=NEW.reviewed_by AND w.workspace_id=NEW.workspace_id) THEN
                RAISE EXCEPTION 'Invalid relationship review context'; END IF;
            IF NEW.status='CONFIRMED' AND (candidate.evidence->>'can_confirm')::boolean IS DISTINCT FROM TRUE THEN
                RAISE EXCEPTION 'Unsafe relationship confirmation'; END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER relationship_run_history BEFORE INSERT OR UPDATE OR DELETE ON relationship_discovery_runs
    FOR EACH ROW EXECUTE FUNCTION protect_relationship_metadata();
CREATE TRIGGER relationship_candidate_history BEFORE INSERT OR UPDATE OR DELETE ON relationship_candidates
    FOR EACH ROW EXECUTE FUNCTION protect_relationship_metadata();
CREATE TRIGGER relationship_review_history BEFORE INSERT OR UPDATE OR DELETE ON relationship_reviews
    FOR EACH ROW EXECUTE FUNCTION protect_relationship_metadata();
