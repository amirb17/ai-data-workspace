-- 0058: immutable pre-send request envelope identity, no provider IO/permits.
-- Explicit migration review required before non-disposable database application.
BEGIN;
CREATE TABLE warehouse_query_request_envelopes (
 attempt_id BIGINT PRIMARY KEY REFERENCES warehouse_query_provider_policies(attempt_id),
 provider_job_id TEXT NOT NULL,
 request_sha256 CHAR(64) NOT NULL CHECK(request_sha256 ~ '^[a-f0-9]{64}$'),
 policy_sha256 CHAR(64) NOT NULL CHECK(policy_sha256 ~ '^[a-f0-9]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION warehouse_query_request_envelope_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP <> 'INSERT' THEN
   RAISE EXCEPTION 'Warehouse request envelope is immutable';
 END IF;
 -- Serialization with send boundary is through the common send-intent row.
 PERFORM 1 FROM warehouse_query_send_intents
   WHERE attempt_id=NEW.attempt_id FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS (
   SELECT 1 FROM warehouse_query_provider_policies p
   JOIN warehouse_query_send_intents i USING(attempt_id)
   JOIN warehouse_query_dispatch_checkpoints c USING(attempt_id)
   JOIN query_governance_reservations r ON r.reservation_id=c.reservation_id
   WHERE p.attempt_id=NEW.attempt_id
     AND p.expected_sha256=NEW.policy_sha256
     AND i.provider_job_id=NEW.provider_job_id
     AND p.expected->>'job_id'=NEW.provider_job_id
     AND r.status='EXECUTING'
     AND c.valid_until>clock_timestamp() AND r.expires_at>clock_timestamp()
     AND NOT EXISTS (
       SELECT 1 FROM warehouse_query_dispatch_revocations v
       WHERE v.attempt_id=NEW.attempt_id
     )
     AND EXISTS (
       SELECT 1 FROM warehouse_query_send_journal j
       WHERE j.attempt_id=NEW.attempt_id AND j.phase='INTENT_RECORDED'
         AND j.event IN ('CREATED','LEASE_ACQUIRED')
         AND j.revision=(
           SELECT MAX(revision) FROM warehouse_query_send_journal
           WHERE attempt_id=NEW.attempt_id
         )
     )
 ) THEN
   RAISE EXCEPTION 'Current pre-send policy and checkpoint required';
 END IF;
 NEW.created_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_request_envelope_guard
 BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_request_envelopes
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_request_envelope_guard();
CREATE TRIGGER warehouse_request_envelope_no_truncate
 BEFORE TRUNCATE ON warehouse_query_request_envelopes
 EXECUTE FUNCTION query_governance_no_truncate();
REVOKE ALL ON warehouse_query_request_envelopes FROM PUBLIC;
REVOKE ALL ON FUNCTION warehouse_query_request_envelope_guard() FROM PUBLIC;
DO $$ BEGIN
 EXECUTE format(
   'ALTER FUNCTION warehouse_query_request_envelope_guard() SET search_path = pg_catalog, %I, pg_temp',
   current_schema()
 );
END $$;
COMMIT;
