-- Additive SQL query metadata only. No source/Gold/metric/answer mutation.
CREATE TABLE saved_sql_queries (
 query_id BIGSERIAL PRIMARY KEY,
 workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
 owner_key TEXT NOT NULL REFERENCES users(owner_key),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
 description TEXT NOT NULL DEFAULT '' CHECK(length(description)<=1000),
 sql_text TEXT NOT NULL CHECK(length(sql_text) BETWEEN 1 AND 20000),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 UNIQUE(workspace_id,owner_key,query_id)
);
CREATE INDEX saved_sql_workspace ON saved_sql_queries(workspace_id,owner_key,updated_at DESC);
CREATE TABLE sql_query_executions (
 execution_id BIGSERIAL PRIMARY KEY,
 workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
 owner_key TEXT NOT NULL REFERENCES users(owner_key),
 saved_query_id BIGINT,
 sql_text TEXT NOT NULL CHECK(length(sql_text) BETWEEN 1 AND 20000),
 query_hash TEXT NOT NULL CHECK(length(query_hash)=64),
 payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 CHECK(payload->>'status' IS NOT NULL AND payload->>'status' IN ('SUCCEEDED','BLOCKED','FAILED','TIMED_OUT'))
);
-- saved_query_id is an immutable historical reference, not a deletion cascade.
CREATE INDEX sql_execution_history ON sql_query_executions(workspace_id,owner_key,execution_id DESC);
CREATE FUNCTION protect_sql_execution() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'SQL execution metadata is immutable'; END $$;
CREATE TRIGGER sql_execution_immutable BEFORE UPDATE ON sql_query_executions
 FOR EACH ROW EXECUTE FUNCTION protect_sql_execution();
-- Controlled owner/workspace-scoped deletion is permitted for bounded history retention.
CREATE FUNCTION validate_sql_metadata_owner() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE TRIGGER saved_sql_owner BEFORE INSERT OR UPDATE ON saved_sql_queries
 FOR EACH ROW EXECUTE FUNCTION validate_sql_metadata_owner();
CREATE TRIGGER sql_execution_owner BEFORE INSERT ON sql_query_executions
 FOR EACH ROW EXECUTE FUNCTION validate_sql_metadata_owner();
