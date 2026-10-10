-- Additive immutable attempts; no existing analytics or definition changes/backfill.
CREATE TABLE workspace_metric_results (
 result_id BIGSERIAL PRIMARY KEY,
 workspace_id BIGINT NOT NULL,
 candidate_id BIGINT NOT NULL,
 definition_version BIGINT NOT NULL REFERENCES metric_discovery_runs(run_id),
 algorithm_version INTEGER NOT NULL CHECK(algorithm_version>0),
 dependency_signature TEXT NOT NULL CHECK(length(dependency_signature)=64),
 plan JSONB NOT NULL CHECK(jsonb_typeof(plan)='object'),
 status TEXT NOT NULL CHECK(status IN ('COMPUTING','FRESH','FAILED')),
 output JSONB,
 failure_code TEXT CHECK(failure_code IN ('EXECUTION_FAILED','INPUT_INVALID','INPUT_LIMIT','INTERRUPTED')),
 started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 computed_at TIMESTAMPTZ,
 FOREIGN KEY(workspace_id,candidate_id) REFERENCES metric_candidates(workspace_id,candidate_id),
 CHECK((status='COMPUTING' AND output IS NULL AND computed_at IS NULL AND failure_code IS NULL)
    OR (status='FRESH' AND output IS NOT NULL AND computed_at IS NOT NULL AND failure_code IS NULL)
    OR (status='FAILED' AND output IS NULL AND computed_at IS NOT NULL AND failure_code IS NOT NULL))
);
CREATE UNIQUE INDEX metric_result_success_identity ON workspace_metric_results(candidate_id,dependency_signature)
 WHERE status='FRESH';
CREATE INDEX metric_result_history ON workspace_metric_results(workspace_id,candidate_id,result_id DESC);
CREATE FUNCTION protect_metric_result() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c metric_candidates;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Metric result history is immutable'; END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.status<>'COMPUTING' OR NEW.status='COMPUTING' OR
   (to_jsonb(OLD)-ARRAY['status','output','failure_code','computed_at']) IS DISTINCT FROM
   (to_jsonb(NEW)-ARRAY['status','output','failure_code','computed_at'])
  THEN RAISE EXCEPTION 'Metric result pins/history are immutable'; END IF;
 ELSE
  SELECT * INTO c FROM metric_candidates WHERE candidate_id=NEW.candidate_id AND workspace_id=NEW.workspace_id;
  IF c.run_id IS DISTINCT FROM NEW.definition_version OR c.evidence->>'validation_status' IS DISTINCT FROM 'VALID'
   OR c.evidence->>'decision'='REVIEW_REQUIRED' OR NEW.plan->'definition' IS DISTINCT FROM c.evidence->'definition'
  THEN RAISE EXCEPTION 'Metric result definition mismatch'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER metric_result_history BEFORE INSERT OR UPDATE OR DELETE ON workspace_metric_results
 FOR EACH ROW EXECUTE FUNCTION protect_metric_result();
