-- Additive candidates/reviews only; no production KPI catalog or processing changes.
CREATE TABLE metric_discovery_runs (
 run_id BIGSERIAL PRIMARY KEY, workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
 owner_key TEXT NOT NULL, run_version INTEGER NOT NULL CHECK(run_version>0),
 algorithm_version INTEGER NOT NULL CHECK(algorithm_version>0), policy_version INTEGER NOT NULL CHECK(policy_version>0),
 source_signature TEXT NOT NULL CHECK(length(source_signature)=64), configuration_key TEXT NOT NULL,
 source_pins JSONB NOT NULL CHECK(jsonb_typeof(source_pins)='array' AND jsonb_array_length(source_pins) BETWEEN 1 AND 20), workspace_suggestion_id BIGINT NOT NULL REFERENCES workspace_semantic_suggestions(suggestion_id),
 provider TEXT NOT NULL, model TEXT NOT NULL, resolved_model TEXT,
 status TEXT NOT NULL CHECK(status IN ('GENERATING','READY','FAILED')),
 failure_code TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), completed_at TIMESTAMPTZ,
 UNIQUE(workspace_id,run_id), UNIQUE(workspace_id,run_version), UNIQUE(workspace_id,source_signature,configuration_key),
 CHECK(status<>'READY' OR (completed_at IS NOT NULL AND failure_code IS NULL))
);
CREATE TABLE metric_candidates (
 candidate_id BIGSERIAL PRIMARY KEY, workspace_id BIGINT NOT NULL, run_id BIGINT NOT NULL,
 formula_key TEXT NOT NULL CHECK(length(formula_key)=64), evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence)='object'),
 FOREIGN KEY(workspace_id,run_id) REFERENCES metric_discovery_runs(workspace_id,run_id),
 UNIQUE(run_id,formula_key), UNIQUE(workspace_id,candidate_id)
);
CREATE TABLE metric_candidate_dependencies (
 candidate_id BIGINT NOT NULL REFERENCES metric_candidates(candidate_id), ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 dependency JSONB NOT NULL CHECK(jsonb_typeof(dependency)='object'), PRIMARY KEY(candidate_id,ordinal)
);
CREATE TABLE metric_reviews (
 review_id BIGSERIAL PRIMARY KEY, workspace_id BIGINT NOT NULL, candidate_id BIGINT NOT NULL,
 review_version INTEGER NOT NULL CHECK(review_version>0), status TEXT NOT NULL CHECK(status IN ('APPROVED','REJECTED')),
 reviewer BIGINT NOT NULL REFERENCES users(user_id), reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 FOREIGN KEY(workspace_id,candidate_id) REFERENCES metric_candidates(workspace_id,candidate_id),
 UNIQUE(candidate_id,review_version)
);
-- Separate exact validated definitions; SYSTEM_POLICY is never an AI approval.
CREATE TABLE approved_metric_definitions (
 metric_id BIGSERIAL PRIMARY KEY, workspace_id BIGINT NOT NULL, candidate_id BIGINT NOT NULL UNIQUE,
 approval_kind TEXT NOT NULL CHECK(approval_kind IN ('SYSTEM_POLICY','USER_REVIEW')),
 review_id BIGINT UNIQUE REFERENCES metric_reviews(review_id),
 definition JSONB NOT NULL CHECK(jsonb_typeof(definition)='object'), dependencies JSONB NOT NULL CHECK(jsonb_typeof(dependencies)='array'),
 policy_version INTEGER NOT NULL CHECK(policy_version>0), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 FOREIGN KEY(workspace_id,candidate_id) REFERENCES metric_candidates(workspace_id,candidate_id),
 CHECK((approval_kind='SYSTEM_POLICY' AND review_id IS NULL) OR (approval_kind='USER_REVIEW' AND review_id IS NOT NULL))
);
CREATE FUNCTION protect_metric_metadata() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE TRIGGER metric_run_history BEFORE INSERT OR UPDATE OR DELETE ON metric_discovery_runs FOR EACH ROW EXECUTE FUNCTION protect_metric_metadata();
CREATE TRIGGER metric_candidate_history BEFORE INSERT OR UPDATE OR DELETE ON metric_candidates FOR EACH ROW EXECUTE FUNCTION protect_metric_metadata();
CREATE TRIGGER metric_dependency_history BEFORE INSERT OR UPDATE OR DELETE ON metric_candidate_dependencies FOR EACH ROW EXECUTE FUNCTION protect_metric_metadata();
CREATE TRIGGER metric_review_history BEFORE INSERT OR UPDATE OR DELETE ON metric_reviews FOR EACH ROW EXECUTE FUNCTION protect_metric_metadata();
CREATE TRIGGER approved_metric_history BEFORE INSERT OR UPDATE OR DELETE ON approved_metric_definitions FOR EACH ROW EXECUTE FUNCTION protect_metric_metadata();
