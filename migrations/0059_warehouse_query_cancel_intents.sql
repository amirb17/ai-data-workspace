-- 0059: immutable cancellation intention; no provider call or claim release.
BEGIN;
CREATE TABLE warehouse_query_cancel_intents (
 attempt_id BIGINT PRIMARY KEY REFERENCES warehouse_query_send_intents(attempt_id),
 provider_job_id TEXT NOT NULL,
 request_sha256 CHAR(64) NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
 journal_revision INTEGER NOT NULL CHECK (journal_revision > 0),
 requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(attempt_id,journal_revision)
  REFERENCES warehouse_query_send_journal(attempt_id,revision)
);
CREATE FUNCTION warehouse_query_cancel_intent_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE pinned RECORD; journal RECORD;
BEGIN
 IF TG_OP <> 'INSERT' THEN
  RAISE EXCEPTION 'Cancellation intent is immutable';
 END IF;
 -- Serialize with original send intent and all journal writes.
 PERFORM 1 FROM warehouse_query_send_intents WHERE attempt_id=NEW.attempt_id FOR UPDATE;
 SELECT e.provider_job_id,e.request_sha256,i.provider_job_id AS original_job
  INTO pinned FROM warehouse_query_request_envelopes e
  JOIN warehouse_query_send_intents i USING(attempt_id)
 WHERE e.attempt_id=NEW.attempt_id;
 SELECT * INTO journal FROM warehouse_query_send_journal
 WHERE attempt_id=NEW.attempt_id ORDER BY revision DESC LIMIT 1;
 IF pinned IS NULL OR journal IS NULL
    OR NEW.provider_job_id<>pinned.provider_job_id
    OR NEW.provider_job_id<>pinned.original_job
    OR NEW.request_sha256<>pinned.request_sha256
    OR NEW.journal_revision<>journal.revision
    OR journal.phase NOT IN ('SEND_UNKNOWN','PROVIDER_RUNNING')
    OR NOT EXISTS(
      SELECT 1 FROM warehouse_query_send_journal
      WHERE attempt_id=NEW.attempt_id AND event='SEND_BOUNDARY'
    )
    OR NOT EXISTS(
      SELECT 1 FROM warehouse_query_dispatch_checkpoints c
      JOIN query_governance_reservations r ON r.reservation_id=c.reservation_id
      WHERE c.attempt_id=NEW.attempt_id AND r.status='EXECUTING'
    )
 THEN
  RAISE EXCEPTION 'Current pinned possible-send job required for cancellation';
 END IF;
 NEW.requested_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_cancel_intent_guard
 BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_cancel_intents
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_cancel_intent_guard();
CREATE TRIGGER warehouse_cancel_intent_no_truncate
 BEFORE TRUNCATE ON warehouse_query_cancel_intents
 EXECUTE FUNCTION query_governance_no_truncate();
REVOKE ALL ON warehouse_query_cancel_intents FROM PUBLIC;
REVOKE ALL ON FUNCTION warehouse_query_cancel_intent_guard() FROM PUBLIC;
DO $$ BEGIN
 EXECUTE format(
  'ALTER FUNCTION warehouse_query_cancel_intent_guard() SET search_path = pg_catalog, %I, pg_temp',
  current_schema()
 );
END $$;
COMMIT;
