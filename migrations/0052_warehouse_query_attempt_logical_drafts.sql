-- Step 414D4a: durable immutable logical SQL draft for a server-issued attempt.
-- A draft is metadata, not an authorization token or provider-ready SQL.
BEGIN;

CREATE TABLE warehouse_query_attempt_logical_drafts (
    attempt_id BIGINT PRIMARY KEY
        REFERENCES warehouse_query_attempt_registry(attempt_id),
    workspace_id BIGINT NOT NULL,
    owner_key TEXT NOT NULL,
    warehouse_model_id BIGINT NOT NULL,
    warehouse_model_version_id BIGINT NOT NULL,
    normalized_sql TEXT NOT NULL
        CHECK(length(normalized_sql) BETWEEN 1 AND 4096),
    projected_columns JSONB NOT NULL
        CHECK(jsonb_typeof(projected_columns)='array'
              AND jsonb_array_length(projected_columns) BETWEEN 1 AND 32),
    row_limit INTEGER NOT NULL CHECK(row_limit BETWEEN 1 AND 500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(attempt_id,workspace_id,owner_key)
        REFERENCES warehouse_query_attempt_registry(
            attempt_id,workspace_id,owner_key
        ),
    FOREIGN KEY(workspace_id,warehouse_model_id,warehouse_model_version_id)
        REFERENCES warehouse_model_versions(
            workspace_id,warehouse_model_id,warehouse_model_version_id
        )
);

CREATE FUNCTION warehouse_query_attempt_logical_draft_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    approved_count INTEGER;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Warehouse logical draft is immutable';
    END IF;
    SELECT count(*) INTO approved_count
    FROM warehouse_models m
    JOIN warehouse_model_versions v
      ON v.warehouse_model_id=m.warehouse_model_id
     AND v.workspace_id=m.workspace_id
    JOIN workspaces w ON w.workspace_id=m.workspace_id
    WHERE m.workspace_id=NEW.workspace_id
      AND m.warehouse_model_id=NEW.warehouse_model_id
      AND v.warehouse_model_version_id=NEW.warehouse_model_version_id
      AND m.status='APPROVED'
      AND v.status='APPROVED'
      AND w.owner=NEW.owner_key;
    IF approved_count<>1 THEN
        RAISE EXCEPTION 'Current owned approved warehouse model required';
    END IF;
    IF EXISTS (
        SELECT 1 FROM warehouse_query_attempt_reservation_links
        WHERE attempt_id=NEW.attempt_id
    ) THEN
        RAISE EXCEPTION 'Cannot attach logical draft after query reservation link';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER warehouse_query_attempt_logical_draft_validate
BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_attempt_logical_drafts
FOR EACH ROW EXECUTE FUNCTION warehouse_query_attempt_logical_draft_guard();

CREATE TRIGGER warehouse_query_attempt_logical_draft_no_truncate
BEFORE TRUNCATE ON warehouse_query_attempt_logical_drafts
FOR EACH STATEMENT EXECUTE FUNCTION query_governance_no_truncate();

COMMIT;
