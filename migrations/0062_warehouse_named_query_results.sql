-- C2 additive protocol metadata, no privilege grants, roles or provider activation.
BEGIN;
ALTER TABLE warehouse_query_api_requests ADD COLUMN contract_version INTEGER NOT NULL DEFAULT 1 CHECK(contract_version IN (1,2));
CREATE TABLE warehouse_query_result_contracts (
 attempt_id BIGINT PRIMARY KEY REFERENCES warehouse_query_api_requests(attempt_id),
 workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
 provider_job_id TEXT NOT NULL UNIQUE,
 sql_sha256 CHAR(64) NOT NULL CHECK(sql_sha256 ~ '^[a-f0-9]{64}$'),
 result JSONB NOT NULL CHECK(jsonb_typeof(result)='object' AND octet_length(result::text)<=32768),
 read_valid_until TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION warehouse_query_named_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a RECORD; held BIGINT;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Named result contract is immutable and permanently held'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('datarise-query-governance-global',0));
 SELECT q.workspace_id,q.request_token,r.reservation_id,r.output_rows,d.projected_columns INTO a
 FROM warehouse_query_attempt_registry q JOIN warehouse_query_api_requests api USING(attempt_id)
 JOIN warehouse_query_attempt_reservation_links l USING(attempt_id)
 JOIN query_governance_reservations r USING(reservation_id)
 JOIN warehouse_query_attempt_logical_drafts d USING(attempt_id)
 WHERE q.attempt_id=NEW.attempt_id AND api.contract_version=2 AND r.status='ACTIVE'
  AND r.expires_at>clock_timestamp() AND q.workspace_id=NEW.workspace_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'Current named API admission required'; END IF;
 IF (NEW.provider_job_id<>'dr_query_w'||a.workspace_id||'_r'||a.reservation_id||'_'||left(a.request_token,32)
   OR NEW.result->>'version'<>'1' OR NEW.result->'destination'->>'datasetId'<>'dr_results_dev_w'||a.workspace_id
   OR NEW.result->'destination'->>'tableId'<>'result_'||encode(sha256(convert_to(NEW.provider_job_id,'UTF8')),'hex')
   OR NEW.result->'destination'->>'projectId' !~ '^[a-z][a-z0-9-]{4,28}[a-z0-9]$'
   OR (NEW.result->>'maximum_rows')::integer<>a.output_rows
   OR (NEW.result->>'maximum_storage_bytes')::bigint<>8388608
   OR (NEW.result->>'workspace_storage_bytes')::bigint<>67108864
   OR NEW.read_valid_until<=clock_timestamp() OR NEW.read_valid_until>clock_timestamp()+interval '1 hour'
   OR floor(extract(epoch FROM NEW.read_valid_until)*1000)::bigint<>(NEW.result->>'read_until_ms')::bigint
   OR (SELECT jsonb_agg(col->0 ORDER BY ord) FROM jsonb_array_elements(NEW.result->'schema') WITH ORDINALITY AS s(col,ord)) IS DISTINCT FROM a.projected_columns
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.result->'schema') col WHERE col->>1 IS NULL OR col->>1 NOT IN ('INTEGER','NUMERIC','BOOLEAN','DATE','TIMESTAMP'))
) IS DISTINCT FROM FALSE THEN RAISE EXCEPTION 'Bounded exact named result identity required'; END IF;
 SELECT count(*)*8388608 INTO held FROM warehouse_query_result_contracts WHERE workspace_id=NEW.workspace_id;
 IF held+8388608>67108864 THEN RAISE EXCEPTION 'Permanent result storage budget exhausted'; END IF;
 NEW.created_at:=clock_timestamp();RETURN NEW;
END $$;
CREATE TRIGGER warehouse_named_result_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_result_contracts FOR EACH ROW EXECUTE FUNCTION warehouse_query_named_result_guard();
CREATE TRIGGER warehouse_named_result_no_truncate BEFORE TRUNCATE ON warehouse_query_result_contracts EXECUTE FUNCTION query_governance_no_truncate();
ALTER TABLE warehouse_query_recovery_attestations DROP CONSTRAINT warehouse_query_recovery_attestations_contract_version_check;
ALTER TABLE warehouse_query_recovery_attestations ADD CHECK(contract_version IN (1,2));
CREATE TABLE warehouse_query_result_attestations (
 attempt_id BIGINT NOT NULL, journal_revision INTEGER NOT NULL,
 metadata JSONB NOT NULL CHECK(jsonb_typeof(metadata)='object' AND octet_length(metadata::text)<=32768),
 metadata_sha256 CHAR(64) NOT NULL CHECK(metadata_sha256 ~ '^[a-f0-9]{64}$'),
 PRIMARY KEY(attempt_id,journal_revision),
 FOREIGN KEY(attempt_id,journal_revision) REFERENCES warehouse_query_recovery_attestations(attempt_id,journal_revision)
);
CREATE FUNCTION warehouse_query_named_attestation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Named result attestation is immutable'; END IF;
 IF NOT EXISTS(SELECT 1 FROM warehouse_query_recovery_attestations a JOIN warehouse_query_result_contracts r USING(attempt_id)
  WHERE a.attempt_id=NEW.attempt_id AND a.journal_revision=NEW.journal_revision AND a.contract_version=2
  AND a.verdict='VERIFIED_METADATA' AND a.observation='SUCCEEDED' AND a.billing_complete
  AND NEW.metadata->'destination'=r.result->'destination'
  AND (NEW.metadata->>'rows')::bigint BETWEEN 0 AND (r.result->>'maximum_rows')::bigint
  AND (NEW.metadata->>'logical_bytes')::bigint BETWEEN 0 AND 8388608)
 THEN RAISE EXCEPTION 'Matching versioned named result proof required'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER warehouse_named_attestation_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_query_result_attestations FOR EACH ROW EXECUTE FUNCTION warehouse_query_named_attestation_guard();
CREATE TRIGGER warehouse_named_attestation_no_truncate BEFORE TRUNCATE ON warehouse_query_result_attestations EXECUTE FUNCTION query_governance_no_truncate();
CREATE OR REPLACE FUNCTION warehouse_query_provider_policy_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Expected provider policy is immutable'; END IF;
 IF NOT EXISTS(
  SELECT 1 FROM warehouse_query_dispatch_checkpoints c
  JOIN warehouse_query_send_intents i USING(attempt_id)
  JOIN query_governance_reservations r ON r.reservation_id=c.reservation_id
  WHERE c.attempt_id=NEW.attempt_id AND r.status='EXECUTING'
   AND c.valid_until>clock_timestamp() AND r.expires_at>clock_timestamp()
   AND NOT EXISTS(SELECT 1 FROM warehouse_query_dispatch_revocations v WHERE v.attempt_id=c.attempt_id)
   AND (NEW.expected->>'contract_version'='1' OR
     (NEW.expected->>'contract_version'='2' AND EXISTS(
       SELECT 1 FROM warehouse_query_result_contracts nr WHERE nr.attempt_id=c.attempt_id
        AND nr.provider_job_id=i.provider_job_id AND nr.sql_sha256=c.compiled_sql_sha256
        AND nr.result=NEW.expected->'result')))
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

CREATE OR REPLACE FUNCTION warehouse_query_recovery_attestation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Recovery attestation is immutable'; END IF;
 IF NOT EXISTS(SELECT 1 FROM warehouse_query_send_journal j WHERE j.attempt_id=NEW.attempt_id
    AND j.revision=NEW.journal_revision AND j.event='OBSERVATION' AND j.observation=NEW.observation)
 THEN RAISE EXCEPTION 'Matching fenced journal observation required'; END IF;
 IF NEW.contract_version=2 AND NOT EXISTS(SELECT 1 FROM warehouse_query_provider_policies p
  JOIN warehouse_query_result_contracts r USING(attempt_id) WHERE p.attempt_id=NEW.attempt_id AND p.expected->>'contract_version'='2')
 THEN RAISE EXCEPTION 'Versioned named policy required'; END IF;
 IF NEW.contract_version=1 AND EXISTS(SELECT 1 FROM warehouse_query_provider_policies p
  WHERE p.attempt_id=NEW.attempt_id AND p.expected->>'contract_version'='2')
 THEN RAISE EXCEPTION 'Named attestation cannot downgrade'; END IF;
 NEW.recorded_at:=clock_timestamp();
 RETURN NEW;
END $$;
CREATE FUNCTION warehouse_query_named_proof_complete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.contract_version=2 AND NEW.verdict='VERIFIED_METADATA' AND NEW.observation='SUCCEEDED'
  AND NOT EXISTS(SELECT 1 FROM warehouse_query_result_attestations p WHERE p.attempt_id=NEW.attempt_id AND p.journal_revision=NEW.journal_revision)
 THEN RAISE EXCEPTION 'Committed named success needs matching result proof'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER warehouse_named_proof_complete AFTER INSERT ON warehouse_query_recovery_attestations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION warehouse_query_named_proof_complete();
REVOKE ALL ON warehouse_query_result_contracts,warehouse_query_result_attestations FROM PUBLIC;
DO $$ DECLARE name TEXT; BEGIN
 FOREACH name IN ARRAY ARRAY['warehouse_query_named_result_guard','warehouse_query_named_attestation_guard','warehouse_query_provider_policy_guard','warehouse_query_recovery_attestation_guard','warehouse_query_named_proof_complete'] LOOP
  EXECUTE format('ALTER FUNCTION %I() SET search_path = pg_catalog, %I, pg_temp',name,current_schema());
  EXECUTE format('REVOKE ALL ON FUNCTION %I() FROM PUBLIC',name);
 END LOOP;
END $$;
COMMIT;
