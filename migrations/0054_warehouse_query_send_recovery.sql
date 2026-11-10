-- 414E1: metadata only. No dispatch, accounting, hold release or grants.
BEGIN;
CREATE TABLE warehouse_query_send_intents (
 attempt_id BIGINT PRIMARY KEY REFERENCES warehouse_query_dispatch_checkpoints(attempt_id),
 reservation_id BIGINT NOT NULL UNIQUE REFERENCES query_governance_reservations(reservation_id),
 provider_project TEXT NOT NULL CHECK(provider_project ~ '^[a-z][a-z0-9-]{4,28}[a-z0-9]$'),
 provider_location TEXT NOT NULL CHECK(provider_location='asia-south1'),
 provider_job_id TEXT NOT NULL,
 compiled_intent_sha256 CHAR(64) NOT NULL CHECK(compiled_intent_sha256 ~ '^[a-f0-9]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(provider_project,provider_location,provider_job_id)
);
CREATE TABLE warehouse_query_send_journal (
 attempt_id BIGINT NOT NULL REFERENCES warehouse_query_send_intents(attempt_id),
 revision INTEGER NOT NULL CHECK(revision>0),
 event TEXT NOT NULL CHECK(event IN ('CREATED','LEASE_ACQUIRED','SEND_BOUNDARY','OBSERVATION')),
 phase TEXT NOT NULL CHECK(phase IN ('INTENT_RECORDED','SEND_UNKNOWN','PROVIDER_RUNNING',
   'SUCCEEDED_OBSERVED','FAILED_OBSERVED','CANCELLED_OBSERVED')),
 observation TEXT CHECK(observation IN ('NOT_FOUND','UNCERTAIN','IN_PROGRESS','SUCCEEDED','FAILED','CANCELLED')),
 lease_token UUID,
 lease_generation INTEGER NOT NULL CHECK(lease_generation>=0),
 lease_until TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(attempt_id,revision),
 CHECK((lease_token IS NULL)=(lease_until IS NULL)),
 CHECK((event='OBSERVATION')=(observation IS NOT NULL))
);
CREATE FUNCTION warehouse_query_send_intent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Send intent is immutable'; END IF;
 IF NOT EXISTS(
  SELECT 1 FROM warehouse_query_dispatch_checkpoints c
  JOIN warehouse_query_attempt_registry a USING(attempt_id)
  JOIN warehouse_query_attempt_reservation_links l USING(attempt_id)
  JOIN query_governance_reservations q ON q.reservation_id=l.reservation_id
  JOIN warehouse_query_attempt_publication_pins p USING(attempt_id)
  JOIN warehouse_resources r USING(resource_id)
  JOIN warehouse_publication_heads h ON h.resource_id=p.resource_id AND h.publication_id=p.publication_id
    AND h.revision=p.publication_revision
  WHERE c.attempt_id=NEW.attempt_id AND c.reservation_id=NEW.reservation_id
   AND q.reservation_id=c.reservation_id AND q.status='EXECUTING'
   AND q.workspace_id=a.workspace_id AND q.owner_key=a.owner_key
   AND q.request_token=btrim(a.request_token) AND q.intent_sha256=c.compiled_intent_sha256
   AND c.compiled_intent_sha256=NEW.compiled_intent_sha256 AND q.consumer='SQL' AND q.provider='BIGQUERY'
   AND c.provider_job_id=NEW.provider_job_id
   AND NEW.provider_job_id='dr_query_w'||c.workspace_id||'_r'||q.reservation_id||'_'||left(q.request_token,32)
   AND r.provider='BIGQUERY' AND r.environment='development' AND r.status='REGISTERED'
   AND r.location=NEW.provider_location AND r.physical_reference->>'project'=NEW.provider_project
   AND split_part(c.evidence->'physical_tables'->>0,'.',1)=NEW.provider_project
   AND c.valid_until>clock_timestamp() AND q.expires_at>clock_timestamp()
   AND NOT EXISTS(SELECT 1 FROM warehouse_query_dispatch_revocations v WHERE v.attempt_id=c.attempt_id)
 ) THEN RAISE EXCEPTION 'Live exact checkpoint required for send intent'; END IF;
 NEW.created_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_send_intent_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_send_intents
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_send_intent_guard();

CREATE FUNCTION warehouse_query_send_journal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE previous warehouse_query_send_journal%ROWTYPE; expected_phase TEXT;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Send journal is append only'; END IF;
 -- Shared serialization point even for the first journal row.
 PERFORM 1 FROM warehouse_query_send_intents WHERE attempt_id=NEW.attempt_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Immutable send intent required'; END IF;
 SELECT * INTO previous FROM warehouse_query_send_journal WHERE attempt_id=NEW.attempt_id
  ORDER BY revision DESC LIMIT 1;
 IF NOT FOUND THEN
  IF NEW.revision<>1 OR NEW.event<>'CREATED' OR NEW.phase<>'INTENT_RECORDED'
   OR NEW.lease_token IS NOT NULL OR NEW.lease_generation<>0 OR NEW.observation IS NOT NULL
  THEN RAISE EXCEPTION 'Initial send journal required'; END IF;
 ELSE
  IF NEW.revision<>previous.revision+1 THEN RAISE EXCEPTION 'Stale send journal revision'; END IF;
  IF NEW.event='LEASE_ACQUIRED' THEN
   IF NEW.phase<>previous.phase OR NEW.lease_token IS NULL
    OR NEW.lease_token IS NOT DISTINCT FROM previous.lease_token
    OR NEW.lease_generation<>previous.lease_generation+1
    OR NEW.lease_until<=clock_timestamp() OR NEW.lease_until>clock_timestamp()+interval '120 seconds'
    OR (previous.lease_until IS NOT NULL AND previous.lease_until>clock_timestamp())
   THEN RAISE EXCEPTION 'Recovery lease unavailable'; END IF;
  ELSE
   IF previous.lease_token IS NULL OR previous.lease_until<=clock_timestamp()
    OR NEW.lease_token IS DISTINCT FROM previous.lease_token
    OR NEW.lease_generation<>previous.lease_generation OR NEW.lease_until IS DISTINCT FROM previous.lease_until
   THEN RAISE EXCEPTION 'Stale or expired recovery owner'; END IF;
   IF NEW.event='SEND_BOUNDARY' THEN
    IF previous.phase<>'INTENT_RECORDED' OR NEW.phase<>'SEND_UNKNOWN' OR NOT EXISTS(
     SELECT 1 FROM warehouse_query_dispatch_checkpoints c JOIN query_governance_reservations q USING(reservation_id)
      WHERE c.attempt_id=NEW.attempt_id AND q.status='EXECUTING' AND c.valid_until>clock_timestamp()
       AND q.expires_at>clock_timestamp() AND NOT EXISTS(
         SELECT 1 FROM warehouse_query_dispatch_revocations v WHERE v.attempt_id=c.attempt_id))
    THEN RAISE EXCEPTION 'Send boundary blocked'; END IF;
   ELSIF NEW.event='OBSERVATION' THEN
    expected_phase:=CASE NEW.observation WHEN 'IN_PROGRESS' THEN 'PROVIDER_RUNNING'
      WHEN 'SUCCEEDED' THEN 'SUCCEEDED_OBSERVED' WHEN 'FAILED' THEN 'FAILED_OBSERVED'
      WHEN 'CANCELLED' THEN 'CANCELLED_OBSERVED' ELSE previous.phase END;
    IF NEW.phase IS DISTINCT FROM expected_phase OR
     (previous.phase IN ('SUCCEEDED_OBSERVED','FAILED_OBSERVED','CANCELLED_OBSERVED') AND NEW.phase<>previous.phase)
    THEN RAISE EXCEPTION 'Recovery phase cannot regress or contradict terminal observation'; END IF;
   ELSE RAISE EXCEPTION 'Invalid send journal transition'; END IF;
  END IF;
 END IF;
 NEW.created_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_send_journal_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_send_journal
 FOR EACH ROW EXECUTE FUNCTION warehouse_query_send_journal_guard();
CREATE TRIGGER warehouse_send_intent_no_truncate BEFORE TRUNCATE ON warehouse_query_send_intents
 EXECUTE FUNCTION query_governance_no_truncate();
CREATE TRIGGER warehouse_send_journal_no_truncate BEFORE TRUNCATE ON warehouse_query_send_journal
 EXECUTE FUNCTION query_governance_no_truncate();
-- Every committed intent must have its initial event in the same transaction.
CREATE FUNCTION warehouse_query_send_initial_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM warehouse_query_send_journal WHERE attempt_id=NEW.attempt_id
  AND revision=1 AND event='CREATED' AND phase='INTENT_RECORDED')
 THEN RAISE EXCEPTION 'Send intent requires atomic initial journal'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER warehouse_send_initial_event_guard AFTER INSERT ON warehouse_query_send_intents
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION warehouse_query_send_initial_event_guard();
REVOKE ALL ON warehouse_query_send_intents,warehouse_query_send_journal FROM PUBLIC;
DO $$ DECLARE trusted TEXT:=current_schema(); name TEXT; BEGIN
 FOREACH name IN ARRAY ARRAY['warehouse_query_send_intent_guard','warehouse_query_send_journal_guard','warehouse_query_send_initial_event_guard'] LOOP
  EXECUTE format('ALTER FUNCTION %I() SET search_path = pg_catalog, %I, pg_temp',name,trusted);
  EXECUTE format('REVOKE ALL ON FUNCTION %I() FROM PUBLIC',name);
 END LOOP;
END $$;
COMMIT;
