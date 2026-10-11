-- Additive, immutable workspace answer history. No ingestion/Gold/metric mutation.
CREATE TABLE workspace_answers (
 answer_id BIGSERIAL PRIMARY KEY,
 workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
 previous_answer_id BIGINT,
 question_hash TEXT NOT NULL CHECK(length(question_hash)=64),
 planner_key TEXT NOT NULL CHECK(length(planner_key)=64),
 cache_key TEXT CHECK(length(cache_key)=64),
 algorithm_version INTEGER NOT NULL CHECK(algorithm_version>0),
 payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
 internal JSONB NOT NULL CHECK(jsonb_typeof(internal)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(workspace_id,answer_id),
 FOREIGN KEY(workspace_id,previous_answer_id) REFERENCES workspace_answers(workspace_id,answer_id),
 CHECK(payload->>'status' IN ('ANSWERED','CLARIFICATION_REQUIRED','REFRESH_REQUIRED','UNSUPPORTED','NO_DATA','FAILED'))
);
CREATE INDEX workspace_answer_history ON workspace_answers(workspace_id,answer_id DESC);
CREATE INDEX workspace_answer_cache ON workspace_answers(workspace_id,cache_key) WHERE payload->>'status'='ANSWERED';
CREATE FUNCTION protect_workspace_answer() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Workspace answer history is immutable';
END $$;
CREATE TRIGGER workspace_answer_history BEFORE UPDATE OR DELETE ON workspace_answers
 FOR EACH ROW EXECUTE FUNCTION protect_workspace_answer();
