-- Durable API idempotency binding; existing attempt/reservation/journal stay authoritative.
BEGIN;
CREATE TABLE warehouse_query_api_requests (
 workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
 user_id BIGINT NOT NULL REFERENCES users(user_id),
 key_sha256 CHAR(64) NOT NULL CHECK(key_sha256 ~ '^[a-f0-9]{64}$'),
 request_sha256 CHAR(64) NOT NULL CHECK(request_sha256 ~ '^[a-f0-9]{64}$'),
 session_digest CHAR(64) NOT NULL CHECK(session_digest ~ '^[a-f0-9]{64}$'),
 attempt_id BIGINT NOT NULL UNIQUE REFERENCES warehouse_query_attempt_registry(attempt_id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,user_id,key_sha256)
);
CREATE FUNCTION warehouse_query_api_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'API request binding is immutable'; END IF;
 IF NOT EXISTS(SELECT 1 FROM warehouse_query_attempt_registry a
   JOIN users u ON u.owner_key=a.owner_key
   JOIN workspaces w ON w.workspace_id=a.workspace_id AND w.owner=u.owner_key
   JOIN auth_sessions s ON s.user_id=u.user_id
   WHERE a.attempt_id=NEW.attempt_id AND a.workspace_id=NEW.workspace_id
    AND u.user_id=NEW.user_id AND u.account_status='ACTIVE' AND w.status='ACTIVE'
    AND s.token_digest=NEW.session_digest AND s.revoked_at IS NULL
    AND s.expires_at>clock_timestamp() AND s.last_seen_at>clock_timestamp()-interval '30 minutes')
 THEN RAISE EXCEPTION 'Current authenticated API request binding required'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_api_request_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_api_requests
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_api_request_guard();
CREATE TRIGGER warehouse_api_request_no_truncate BEFORE TRUNCATE ON warehouse_query_api_requests
 EXECUTE FUNCTION query_governance_no_truncate();
REVOKE ALL ON warehouse_query_api_requests FROM PUBLIC;
REVOKE ALL ON FUNCTION warehouse_query_api_request_guard() FROM PUBLIC;
DO $$ BEGIN
 EXECUTE format('ALTER FUNCTION warehouse_query_api_request_guard() SET search_path = pg_catalog, %I, pg_temp',current_schema());
END $$;
COMMIT;
