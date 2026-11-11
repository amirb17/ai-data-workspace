-- 414E3 exact server-owned expected policy. No send, grant or hold release.
BEGIN;
CREATE TABLE warehouse_query_provider_policies (
 attempt_id BIGINT PRIMARY KEY REFERENCES warehouse_query_send_intents(attempt_id),
 expected JSONB NOT NULL CHECK(jsonb_typeof(expected)='object' AND octet_length(expected::text)<=131072),
 expected_sha256 CHAR(64) NOT NULL CHECK(expected_sha256 ~ '^[a-f0-9]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION warehouse_query_provider_policy_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Expected provider policy is immutable'; END IF;
 IF NOT EXISTS(
  SELECT 1 FROM warehouse_query_dispatch_checkpoints c
  JOIN warehouse_query_send_intents i USING(attempt_id)
  JOIN query_governance_reservations r ON r.reservation_id=c.reservation_id
  WHERE c.attempt_id=NEW.attempt_id AND r.status='EXECUTING'
   AND c.valid_until>clock_timestamp() AND r.expires_at>clock_timestamp()
   AND NOT EXISTS(SELECT 1 FROM warehouse_query_dispatch_revocations v WHERE v.attempt_id=c.attempt_id)
   AND NEW.expected->>'contract_version'='1'
   AND NEW.expected->>'project'=i.provider_project
   AND NEW.expected->>'location'=i.provider_location
   AND NEW.expected->>'dataset'='dr_dev_w'||c.workspace_id
   AND NEW.expected->>'job_id'=i.provider_job_id
   AND NEW.expected->>'sql_sha256'=c.compiled_sql_sha256
   AND NEW.expected->>'compiled_intent_sha256'=c.compiled_intent_sha256
   AND NEW.expected->'labels'=jsonb_build_object('datarise','query','workspace',c.workspace_id::text,
       'reservation',r.reservation_id::text,'intent',left(c.compiled_intent_sha256,63))
   AND (NEW.expected->>'maximum_bytes_billed')::bigint BETWEEN 1 AND LEAST(r.reserved_bytes,r.maximum_bytes_billed)
 ) THEN RAISE EXCEPTION 'Live exact expected provider policy required'; END IF;
 NEW.created_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_provider_policy_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_provider_policies
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_provider_policy_guard();
CREATE TRIGGER warehouse_provider_policy_no_truncate BEFORE TRUNCATE ON warehouse_query_provider_policies
 EXECUTE FUNCTION query_governance_no_truncate();
REVOKE ALL ON warehouse_query_provider_policies FROM PUBLIC;
REVOKE ALL ON FUNCTION warehouse_query_provider_policy_guard() FROM PUBLIC;
DO $$ BEGIN
 EXECUTE format('ALTER FUNCTION warehouse_query_provider_policy_guard() SET search_path = pg_catalog, %I, pg_temp',current_schema());
END $$;
COMMIT;
