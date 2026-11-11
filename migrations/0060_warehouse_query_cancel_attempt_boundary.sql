-- 0060: one-way cancellation-attempt boundary. NO provider IO, grants or retry permits.
BEGIN;
CREATE TABLE warehouse_query_cancel_attempt_boundaries (
 attempt_id BIGINT PRIMARY KEY REFERENCES warehouse_query_cancel_intents(attempt_id),
 provider_job_id TEXT NOT NULL,
 request_sha256 CHAR(64) NOT NULL CHECK(request_sha256 ~ '^[a-f0-9]{64}$'),
 journal_revision INTEGER NOT NULL CHECK(journal_revision > 0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (attempt_id,journal_revision)
  REFERENCES warehouse_query_send_journal(attempt_id,revision)
);
CREATE FUNCTION warehouse_query_cancel_attempt_boundary_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE original RECORD; latest RECORD; reservation_status TEXT;
BEGIN
 IF TG_OP<>'INSERT' THEN
  RAISE EXCEPTION 'Cancellation attempt boundary is immutable';
 END IF;
 -- Serialize with send journal and all cancellation workers.
 PERFORM 1 FROM warehouse_query_send_intents
 WHERE attempt_id=NEW.attempt_id FOR UPDATE;
 SELECT i.provider_job_id, i.request_sha256,
        e.provider_job_id AS envelope_job, e.request_sha256 AS envelope_hash
   INTO original
 FROM warehouse_query_cancel_intents i
 JOIN warehouse_query_request_envelopes e USING(attempt_id)
 WHERE i.attempt_id=NEW.attempt_id;
 SELECT * INTO latest FROM warehouse_query_send_journal
 WHERE attempt_id=NEW.attempt_id ORDER BY revision DESC LIMIT 1;
 SELECT r.status INTO reservation_status
 FROM warehouse_query_dispatch_checkpoints c
 JOIN query_governance_reservations r ON r.reservation_id=c.reservation_id
 WHERE c.attempt_id=NEW.attempt_id;
 IF original IS NULL OR latest IS NULL
   OR NEW.provider_job_id<>original.provider_job_id
   OR NEW.provider_job_id<>original.envelope_job
   OR NEW.request_sha256<>original.request_sha256
   OR NEW.request_sha256<>original.envelope_hash
   OR NEW.journal_revision<>latest.revision
   OR latest.phase NOT IN ('SEND_UNKNOWN','PROVIDER_RUNNING')
   OR latest.lease_token IS NULL
   OR latest.lease_until<=clock_timestamp()
   OR reservation_status<>'EXECUTING'
   OR NOT EXISTS(
     SELECT 1 FROM warehouse_query_send_journal
     WHERE attempt_id=NEW.attempt_id AND event='SEND_BOUNDARY'
   )
 THEN
   RAISE EXCEPTION 'Current fenced cancellation context required';
 END IF;
 NEW.created_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_cancel_attempt_boundary_guard
 BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_cancel_attempt_boundaries
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_cancel_attempt_boundary_guard();
CREATE TRIGGER warehouse_cancel_attempt_boundary_no_truncate
 BEFORE TRUNCATE ON warehouse_query_cancel_attempt_boundaries
 EXECUTE FUNCTION query_governance_no_truncate();
REVOKE ALL ON warehouse_query_cancel_attempt_boundaries FROM PUBLIC;
REVOKE ALL ON FUNCTION warehouse_query_cancel_attempt_boundary_guard() FROM PUBLIC;
DO $$ BEGIN
 EXECUTE format('ALTER FUNCTION warehouse_query_cancel_attempt_boundary_guard() SET search_path = pg_catalog, %I, pg_temp', current_schema());
END $$;
COMMIT;
