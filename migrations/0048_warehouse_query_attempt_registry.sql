-- Step 414B: server-owned warehouse query attempt registry.
-- Standalone metadata only; no BigQuery dispatch, budget or reservation claim.
BEGIN;

CREATE TABLE warehouse_query_attempt_registry (
    attempt_id BIGSERIAL PRIMARY KEY,
    attempt_handle CHAR(32) NOT NULL UNIQUE
        CHECK(attempt_handle ~ '^[a-f0-9]{32}$'),
    request_token CHAR(64) NOT NULL UNIQUE
        CHECK(request_token ~ '^[a-f0-9]{64}$'),
    workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
    owner_key TEXT NOT NULL REFERENCES users(owner_key),
    logical_intent_sha256 CHAR(64) NOT NULL
        CHECK(logical_intent_sha256 ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(attempt_id, workspace_id, owner_key)
);

CREATE INDEX warehouse_query_attempt_registry_owner
    ON warehouse_query_attempt_registry(workspace_id,owner_key,created_at);

CREATE FUNCTION warehouse_query_attempt_registry_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Warehouse query attempt identity is immutable';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM workspaces
        WHERE workspace_id=NEW.workspace_id AND owner=NEW.owner_key
    ) THEN
        RAISE EXCEPTION 'Warehouse query attempt requires current workspace owner';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER warehouse_query_attempt_registry_validate
BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_attempt_registry
FOR EACH ROW EXECUTE FUNCTION warehouse_query_attempt_registry_guard();

CREATE TRIGGER warehouse_query_attempt_registry_no_truncate
BEFORE TRUNCATE ON warehouse_query_attempt_registry
FOR EACH STATEMENT EXECUTE FUNCTION query_governance_no_truncate();

COMMIT;
