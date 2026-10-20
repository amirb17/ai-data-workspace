-- DOCUMENTED SCHEMA ROLLBACK ONLY. Never run after an adoption event exists.
BEGIN;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ownership_adoption_events) THEN
  RAISE EXCEPTION 'Reverse/verify adoption separately; schema rollback requires empty provenance';
 END IF;
END $$;
CREATE OR REPLACE FUNCTION validate_semantic_profile_head() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.current_state_id IS DISTINCT FROM OLD.current_state_id THEN NEW.profile_status:='STALE'; END IF;
    IF NEW.current_profile_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM semantic_profiles p
        WHERE p.profile_id=NEW.current_profile_id AND p.dataset_id=NEW.dataset_id AND p.workspace_id=NEW.workspace_id
        AND p.owner_key=NEW.owner AND p.status='READY'
        AND (NEW.profile_status<>'READY' OR p.source_state_id=NEW.current_state_id)) THEN RAISE EXCEPTION 'Profile pointer must reference owned successful evidence'; END IF;
    IF NEW.profile_status='READY' AND NEW.current_profile_id IS NULL THEN RAISE EXCEPTION 'Ready profile requires evidence'; END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION validate_semantic_suggestion_pins() RETURNS trigger LANGUAGE plpgsql AS $$
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

CREATE OR REPLACE FUNCTION validate_sql_metadata_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM workspaces WHERE workspace_id=NEW.workspace_id AND owner=NEW.owner_key) THEN
  RAISE EXCEPTION 'SQL metadata must belong to workspace owner';
 END IF;
 IF TG_OP='UPDATE' AND (NEW.workspace_id<>OLD.workspace_id OR NEW.owner_key<>OLD.owner_key) THEN
  RAISE EXCEPTION 'SQL metadata ownership is immutable';
 END IF;
 IF TG_TABLE_NAME='sql_query_executions' THEN
  IF NEW.saved_query_id IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM saved_sql_queries WHERE query_id=NEW.saved_query_id AND workspace_id=NEW.workspace_id AND owner_key=NEW.owner_key) THEN
    RAISE EXCEPTION 'Saved query must belong to execution workspace owner';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION protect_workspace_semantic_suggestion() RETURNS trigger LANGUAGE plpgsql AS $$
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

CREATE OR REPLACE FUNCTION protect_relationship_metadata() RETURNS trigger LANGUAGE plpgsql AS $$
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

CREATE OR REPLACE FUNCTION protect_metric_metadata() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c metric_candidates; r metric_discovery_runs; decision metric_reviews; pin JSONB;
BEGIN
 IF TG_TABLE_NAME='metric_discovery_runs' THEN
  IF TG_OP='DELETE' OR (TG_OP='UPDATE' AND OLD.status='READY') THEN RAISE EXCEPTION 'Metric run history is immutable'; END IF;
  IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['status','failure_code','completed_at','resolved_model']) IS DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['status','failure_code','completed_at','resolved_model']) THEN RAISE EXCEPTION 'Metric source pins are immutable'; END IF;
  IF TG_OP='INSERT' OR NEW.status='READY' THEN
   IF NOT EXISTS(SELECT 1 FROM workspace_semantic_suggestions s JOIN workspaces w USING(workspace_id)
    WHERE s.suggestion_id=NEW.workspace_suggestion_id AND s.workspace_id=NEW.workspace_id AND s.status='READY' AND w.owner=NEW.owner_key)
    THEN RAISE EXCEPTION 'Metric workspace context mismatch'; END IF;
   FOR pin IN SELECT value FROM jsonb_array_elements(NEW.source_pins) LOOP
    IF NOT EXISTS(SELECT 1 FROM datasets d JOIN semantic_profiles p ON p.profile_id=d.current_profile_id
     WHERE d.dataset_id=(pin->>'dataset_id')::bigint AND d.workspace_id=NEW.workspace_id AND d.owner=NEW.owner_key
     AND d.status<>'ARCHIVED' AND d.profile_status='READY' AND p.status='READY' AND p.source_state_id=d.current_state_id
     AND p.profile_id=(pin->>'profile_id')::bigint AND d.current_state_id=(pin->>'state_id')::bigint)
     THEN RAISE EXCEPTION 'Metric dataset context mismatch'; END IF;
   END LOOP;
  END IF;
 ELSE
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Metric definition/review history is immutable'; END IF;
  IF TG_TABLE_NAME='metric_candidates' THEN
   SELECT * INTO r FROM metric_discovery_runs WHERE workspace_id=NEW.workspace_id AND run_id=NEW.run_id;
   IF r.status IS DISTINCT FROM 'GENERATING' THEN RAISE EXCEPTION 'Metric candidate context mismatch'; END IF;
  ELSIF TG_TABLE_NAME='metric_candidate_dependencies' THEN
   SELECT r0.* INTO r FROM metric_candidates c0 JOIN metric_discovery_runs r0 USING(run_id) WHERE c0.candidate_id=NEW.candidate_id;
   IF r.status IS DISTINCT FROM 'GENERATING' THEN RAISE EXCEPTION 'Metric dependency context mismatch'; END IF;
  ELSE
   SELECT * INTO c FROM metric_candidates WHERE candidate_id=NEW.candidate_id AND workspace_id=NEW.workspace_id;
   SELECT * INTO r FROM metric_discovery_runs WHERE run_id=c.run_id;
   IF c.candidate_id IS NULL THEN RAISE EXCEPTION 'Metric ownership mismatch'; END IF;
   IF TG_TABLE_NAME='metric_reviews' THEN
    IF r.status IS DISTINCT FROM 'READY' OR NOT EXISTS(SELECT 1 FROM users u JOIN workspaces w ON w.owner=u.owner_key
     WHERE u.user_id=NEW.reviewer AND w.workspace_id=NEW.workspace_id) THEN RAISE EXCEPTION 'Metric review context mismatch'; END IF;
    IF NEW.status='APPROVED' AND c.evidence->>'validation_status' IS DISTINCT FROM 'VALID' THEN RAISE EXCEPTION 'Unsafe metric approval'; END IF;
   ELSE
    IF c.evidence->>'validation_status' IS DISTINCT FROM 'VALID' OR NEW.definition IS DISTINCT FROM c.evidence->'definition'
     OR NEW.dependencies IS DISTINCT FROM c.evidence->'dependencies' OR NEW.policy_version<>r.policy_version THEN RAISE EXCEPTION 'Approved metric differs from validated candidate'; END IF;
    IF NEW.approval_kind='SYSTEM_POLICY' AND (r.status NOT IN ('GENERATING','READY') OR c.evidence->>'decision' IS DISTINCT FROM 'AUTO_ACCEPT')
     THEN RAISE EXCEPTION 'Unsafe automatic acceptance'; END IF;
    IF NEW.approval_kind='USER_REVIEW' THEN
     SELECT * INTO decision FROM metric_reviews WHERE review_id=NEW.review_id;
     IF decision.candidate_id IS DISTINCT FROM NEW.candidate_id OR decision.status IS DISTINCT FROM 'APPROVED' THEN RAISE EXCEPTION 'Metric review mismatch'; END IF;
    END IF;
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER adoption_event_insert ON ownership_adoption_events;
DROP TRIGGER adoption_event_immutable ON ownership_adoption_events;
DROP TRIGGER adoption_event_no_truncate ON ownership_adoption_events;
DROP TRIGGER adoption_commit_integrity ON ownership_adoption_events;
DROP FUNCTION validate_adoption_event();
DROP FUNCTION validate_adoption_commit();
DROP FUNCTION adoption_history_actor_matches(bigint,bigint,text,text);
DROP TABLE ownership_adoption_events;
DROP FUNCTION adoption_scope_valid(jsonb,integer,integer);
COMMIT;
