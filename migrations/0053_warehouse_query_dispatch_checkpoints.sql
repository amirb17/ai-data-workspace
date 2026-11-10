-- 414D5: non-dispatch checkpoint, append-only revocations, conservative holds.
-- No cloud send, grants, roles, cleanup permission or executable permit.
BEGIN;
CREATE TABLE warehouse_query_dispatch_checkpoints (
 attempt_id BIGINT PRIMARY KEY REFERENCES warehouse_query_attempt_registry(attempt_id),
 reservation_id BIGINT NOT NULL UNIQUE REFERENCES query_governance_reservations(reservation_id),
 workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
 user_id BIGINT NOT NULL REFERENCES users(user_id),
 -- No FK: auth session pruning must remain possible and append revocation.
 session_digest CHAR(64) NOT NULL CHECK(session_digest ~ '^[a-f0-9]{64}$'),
 compiled_intent_sha256 CHAR(64) NOT NULL CHECK(compiled_intent_sha256 ~ '^[a-f0-9]{64}$'),
 compiled_sql_sha256 CHAR(64) NOT NULL CHECK(compiled_sql_sha256 ~ '^[a-f0-9]{64}$'),
 provider_job_id TEXT NOT NULL UNIQUE,
 evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence)='object' AND octet_length(evidence::text)<=131072),
 policy JSONB NOT NULL CHECK(jsonb_typeof(policy)='object'),
 valid_until TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK(valid_until>created_at)
);
CREATE INDEX warehouse_dispatch_session ON warehouse_query_dispatch_checkpoints(session_digest);
CREATE INDEX warehouse_dispatch_user ON warehouse_query_dispatch_checkpoints(user_id);
CREATE INDEX warehouse_dispatch_workspace ON warehouse_query_dispatch_checkpoints(workspace_id);
CREATE TABLE warehouse_query_dispatch_revocations (
 attempt_id BIGINT NOT NULL REFERENCES warehouse_query_dispatch_checkpoints(attempt_id),
 reason TEXT NOT NULL CHECK(reason IN ('SESSION_CHANGED','ACCOUNT_CHANGED','WORKSPACE_CHANGED',
   'PUBLICATION_CHANGED','RESOURCE_CHANGED','SOURCE_CHANGED','MODEL_CHANGED')),
 revoked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(attempt_id,reason)
);
CREATE FUNCTION warehouse_query_dispatch_record_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Dispatch checkpoint history is immutable'; END IF;
 IF TG_TABLE_NAME='warehouse_query_dispatch_checkpoints' AND NOT EXISTS(
   SELECT 1 FROM warehouse_query_attempt_registry a
   JOIN warehouse_query_attempt_reservation_links l USING(attempt_id)
   JOIN query_governance_reservations r ON r.reservation_id=l.reservation_id
   JOIN warehouse_query_attempt_logical_drafts d USING(attempt_id)
   JOIN users u ON u.user_id=NEW.user_id AND u.owner_key=a.owner_key
   JOIN workspaces w ON w.workspace_id=a.workspace_id AND w.owner=a.owner_key
   JOIN auth_sessions s ON s.user_id=u.user_id AND s.token_digest=NEW.session_digest
   WHERE a.attempt_id=NEW.attempt_id AND a.workspace_id=NEW.workspace_id
    AND r.reservation_id=NEW.reservation_id AND r.status='EXECUTING'
    AND r.workspace_id=a.workspace_id AND r.owner_key=a.owner_key
    AND r.request_token=btrim(a.request_token) AND r.consumer='SQL' AND r.provider='BIGQUERY'
    AND r.intent_sha256=NEW.compiled_intent_sha256 AND r.output_rows=d.row_limit
    AND u.account_status='ACTIVE' AND w.status='ACTIVE'
    AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
    AND s.last_seen_at>clock_timestamp()-interval '30 minutes'
    AND NEW.valid_until>clock_timestamp()
    AND NEW.valid_until<=LEAST(s.expires_at,s.last_seen_at+interval '30 minutes',r.expires_at)
 ) THEN RAISE EXCEPTION 'Verified live dispatch checkpoint identity required'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_query_dispatch_checkpoint_guard
 BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_dispatch_checkpoints
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_record_guard();
CREATE TRIGGER warehouse_query_dispatch_revocation_guard
 BEFORE UPDATE OR DELETE ON warehouse_query_dispatch_revocations
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_record_guard();
CREATE TRIGGER warehouse_query_dispatch_checkpoint_no_truncate
 BEFORE TRUNCATE ON warehouse_query_dispatch_checkpoints EXECUTE FUNCTION query_governance_no_truncate();
CREATE TRIGGER warehouse_query_dispatch_revocation_no_truncate
 BEFORE TRUNCATE ON warehouse_query_dispatch_revocations EXECUTE FUNCTION query_governance_no_truncate();

-- A checkpoint itself is a hold. No automatic release on expiry or settlement.
CREATE OR REPLACE FUNCTION warehouse_query_publication_dependency_present(
 target_resource_id BIGINT,target_publication_id BIGINT
) RETURNS BOOLEAN LANGUAGE sql VOLATILE AS $$
 SELECT EXISTS(
  SELECT 1 FROM warehouse_query_attempt_publication_pins p
  JOIN warehouse_query_attempt_reservation_links l USING(attempt_id)
  JOIN query_governance_reservations r USING(reservation_id)
  LEFT JOIN warehouse_query_dispatch_checkpoints c ON c.attempt_id=p.attempt_id
  WHERE p.resource_id=target_resource_id AND p.publication_id=target_publication_id
   AND (c.attempt_id IS NOT NULL OR r.status='EXECUTING'
     OR (r.status='ACTIVE' AND r.expires_at>clock_timestamp()))
 )
$$;

CREATE FUNCTION warehouse_query_dispatch_revoke_notice() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE oldrow JSONB:=to_jsonb(OLD); category TEXT;
BEGIN
 IF TG_OP='UPDATE' AND to_jsonb(NEW)=oldrow THEN RETURN NEW; END IF;
 category:=CASE TG_TABLE_NAME
  WHEN 'auth_sessions' THEN 'SESSION_CHANGED' WHEN 'users' THEN 'ACCOUNT_CHANGED'
  WHEN 'workspaces' THEN 'WORKSPACE_CHANGED' WHEN 'warehouse_publication_heads' THEN 'PUBLICATION_CHANGED'
  WHEN 'warehouse_resources' THEN 'RESOURCE_CHANGED' WHEN 'datasets' THEN 'SOURCE_CHANGED'
  ELSE 'MODEL_CHANGED' END;
 INSERT INTO warehouse_query_dispatch_revocations(attempt_id,reason)
 SELECT c.attempt_id,category FROM warehouse_query_dispatch_checkpoints c
 JOIN warehouse_query_attempt_logical_drafts d USING(attempt_id)
 WHERE (TG_TABLE_NAME='auth_sessions' AND c.session_digest=oldrow->>'token_digest')
 OR (TG_TABLE_NAME='users' AND c.user_id=(oldrow->>'user_id')::bigint)
 OR (TG_TABLE_NAME='workspaces' AND c.workspace_id=(oldrow->>'workspace_id')::bigint)
 OR (TG_TABLE_NAME='warehouse_models' AND d.warehouse_model_id=(oldrow->>'warehouse_model_id')::bigint)
 OR (TG_TABLE_NAME='warehouse_model_versions' AND d.warehouse_model_version_id=(oldrow->>'warehouse_model_version_id')::bigint)
 OR (TG_TABLE_NAME IN ('warehouse_publication_heads','warehouse_resources') AND EXISTS(
   SELECT 1 FROM warehouse_query_attempt_publication_pins p WHERE p.attempt_id=c.attempt_id
    AND p.resource_id=(oldrow->>'resource_id')::bigint
    AND (TG_TABLE_NAME='warehouse_resources' OR p.publication_id=(oldrow->>'publication_id')::bigint)))
 OR (TG_TABLE_NAME='datasets' AND EXISTS(SELECT 1 FROM warehouse_model_sources s
    WHERE s.warehouse_model_version_id=d.warehouse_model_version_id
      AND s.source_dataset_id=(oldrow->>'dataset_id')::bigint))
 ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE TRIGGER warehouse_dispatch_session_revoke AFTER UPDATE OF revoked_at,expires_at OR DELETE ON auth_sessions
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_revoke_notice();
CREATE TRIGGER warehouse_dispatch_account_revoke AFTER UPDATE OF account_status,owner_key ON users
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_revoke_notice();
CREATE TRIGGER warehouse_dispatch_workspace_revoke AFTER UPDATE OF owner,status ON workspaces
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_revoke_notice();
CREATE TRIGGER warehouse_dispatch_publication_revoke AFTER UPDATE OR DELETE ON warehouse_publication_heads
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_revoke_notice();
CREATE TRIGGER warehouse_dispatch_resource_revoke AFTER UPDATE OR DELETE ON warehouse_resources
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_revoke_notice();
CREATE TRIGGER warehouse_dispatch_source_revoke AFTER UPDATE OF owner,status,current_state_id ON datasets
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_revoke_notice();
CREATE TRIGGER warehouse_dispatch_model_revoke AFTER UPDATE OF status ON warehouse_models
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_revoke_notice();
CREATE TRIGGER warehouse_dispatch_version_revoke AFTER UPDATE OF status ON warehouse_model_versions
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_dispatch_revoke_notice();
REVOKE ALL ON warehouse_query_dispatch_checkpoints,warehouse_query_dispatch_revocations FROM PUBLIC;
DO $$ DECLARE trusted TEXT:=current_schema(); name TEXT; BEGIN
 FOREACH name IN ARRAY ARRAY['warehouse_query_dispatch_record_guard','warehouse_query_dispatch_revoke_notice'] LOOP
  EXECUTE format('ALTER FUNCTION %I() SET search_path = pg_catalog, %I, pg_temp',name,trusted);
  EXECUTE format('REVOKE ALL ON FUNCTION %I() FROM PUBLIC',name);
 END LOOP;
 EXECUTE format('ALTER FUNCTION warehouse_query_publication_dependency_present(bigint,bigint) SET search_path = pg_catalog, %I, pg_temp',trusted);
END $$;
COMMIT;
