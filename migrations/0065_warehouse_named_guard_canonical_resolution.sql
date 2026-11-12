-- 0065: Restore canonical qualified reads in named-result admission guard.
-- The exact trigger join has been verified true by the rollback-only probe.
-- Avoid dependency on a stored search_path or PL/pgSQL FOUND state.
-- Keep ALL identity, temporal, schema, budget, and immutability validations.
BEGIN;
DO $guard$
DECLARE fn oid; tg oid;
BEGIN
 IF current_database()<>'ai_data_workspace'
     OR current_schema()<>'datarise_warehouse_dev'
     OR session_user<>'postgres' THEN
   RAISE EXCEPTION '0065 requires local canonical warehouse schema and admin';
 END IF;
 SELECT to_regprocedure('datarise_warehouse_dev.warehouse_query_named_result_guard()') INTO fn;
 SELECT t.oid INTO tg FROM pg_trigger t
 JOIN pg_class c ON c.oid=t.tgrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='datarise_warehouse_dev'
   AND c.relname='warehouse_query_result_contracts'
   AND t.tgname='warehouse_named_result_guard'
   AND t.tgfoid=fn AND t.tgenabled='O' AND NOT t.tgisinternal;
 IF fn IS NULL OR tg IS NULL THEN
   RAISE EXCEPTION '0062 named result admission trigger is missing';
 END IF;
END $guard$;
CREATE OR REPLACE FUNCTION datarise_warehouse_dev.warehouse_query_named_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a RECORD; held BIGINT;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Named result contract is immutable and permanently held'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('datarise-query-governance-global',0));
 SELECT q.workspace_id,q.request_token,r.reservation_id,r.output_rows,d.projected_columns INTO a
 FROM datarise_warehouse_dev.warehouse_query_attempt_registry q JOIN datarise_warehouse_dev.warehouse_query_api_requests api USING(attempt_id)
 JOIN datarise_warehouse_dev.warehouse_query_attempt_reservation_links l USING(attempt_id)
 JOIN public.query_governance_reservations r USING(reservation_id)
 JOIN datarise_warehouse_dev.warehouse_query_attempt_logical_drafts d USING(attempt_id)
 WHERE q.attempt_id=NEW.attempt_id AND api.contract_version=2 AND r.status='ACTIVE'
  AND r.expires_at>clock_timestamp() AND q.workspace_id=NEW.workspace_id;
 IF a.reservation_id IS NULL THEN
    RAISE EXCEPTION 'Current named API admission required';
 END IF;
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
 SELECT count(*)*8388608 INTO held FROM datarise_warehouse_dev.warehouse_query_result_contracts WHERE workspace_id=NEW.workspace_id;
 IF held+8388608>67108864 THEN RAISE EXCEPTION 'Permanent result storage budget exhausted'; END IF;
 NEW.created_at:=clock_timestamp();RETURN NEW;
END $$;

ALTER FUNCTION datarise_warehouse_dev.warehouse_query_named_result_guard()
 SET search_path=pg_catalog, datarise_warehouse_dev, public, pg_temp;
REVOKE ALL ON FUNCTION datarise_warehouse_dev.warehouse_query_named_result_guard() FROM PUBLIC;
COMMIT;
