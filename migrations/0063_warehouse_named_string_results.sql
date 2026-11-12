-- 0063: Allow bounded STRING projections in named warehouse result contracts.
-- Same immutable destination, 500-row, 8-MiB/table and 64-MiB/workspace
-- guarantees as 0062. Application additionally limits cells and first-page
-- responses. Never run on an unreviewed database or public schema.
BEGIN;
DO $guard$
BEGIN
 IF current_schema()='public' OR current_schema() LIKE 'pg_%' THEN
  RAISE EXCEPTION '0063 requires the dedicated warehouse query schema';
 END IF;
 IF to_regclass(format('%I.warehouse_query_result_contracts',current_schema())) IS NULL THEN
  RAISE EXCEPTION '0062 named-result contracts required';
 END IF;
END $guard$;
CREATE OR REPLACE FUNCTION warehouse_query_named_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.result->'schema') col WHERE col->>1 IS NULL OR col->>1 NOT IN ('STRING','INTEGER','NUMERIC','BOOLEAN','DATE','TIMESTAMP'))
) IS DISTINCT FROM FALSE THEN RAISE EXCEPTION 'Bounded exact named result identity required'; END IF;
 SELECT count(*)*8388608 INTO held FROM warehouse_query_result_contracts WHERE workspace_id=NEW.workspace_id;
 IF held+8388608>67108864 THEN RAISE EXCEPTION 'Permanent result storage budget exhausted'; END IF;
 NEW.created_at:=clock_timestamp();RETURN NEW;
END $$;

DO $acl$
BEGIN
 EXECUTE format('ALTER FUNCTION %I.warehouse_query_named_result_guard() SET search_path = pg_catalog, %I, pg_temp',current_schema(),current_schema());
 EXECUTE format('REVOKE ALL ON FUNCTION %I.warehouse_query_named_result_guard() FROM PUBLIC',current_schema());
END $acl$;
COMMIT;
