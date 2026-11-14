-- Fix PL/pgSQL collision between argument catalog and dataset_gold_runs.catalog.
-- Preserves signature, ownership, SECURITY DEFINER, grants and all state fencing.
BEGIN;
CREATE OR REPLACE FUNCTION task21_analytics_publish(actor BIGINT,w BIGINT,d BIGINT,
  runid BIGINT,key TEXT,checksum TEXT,catalog JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE r dataset_gold_runs; actual BIGINT; source_rows BIGINT;
BEGIN
 PERFORM task21_analytics_owner(actor,w,d);
 SELECT current_state_id INTO actual FROM datasets
 WHERE dataset_id=d AND workspace_id=w FOR UPDATE;
 SELECT * INTO r FROM dataset_gold_runs WHERE gold_run_id=runid AND dataset_id=d FOR UPDATE;
 IF NOT FOUND OR r.status<>'REFRESHING' THEN
    RAISE EXCEPTION 'Analytics run is not publishable';
 END IF;
 IF checksum !~ '^[a-f0-9]{64}$'
    OR key IS NULL OR key NOT LIKE
       ('gold/dataset_id='||d||'/state_id='||r.source_state_id||
        '/run_id='||r.gold_run_id||'/%/manifest.json')
    OR jsonb_typeof(catalog) IS DISTINCT FROM 'array'
    OR jsonb_array_length(catalog)<1
    OR catalog->0->>'artifact_type' IS DISTINCT FROM 'BASE'
    OR (catalog->0->>'gold_run_id')::bigint IS DISTINCT FROM runid
 THEN RAISE EXCEPTION 'Invalid cumulative Gold publication evidence'; END IF;
 SELECT row_count INTO source_rows FROM dataset_state_versions
 WHERE state_id=r.source_state_id AND dataset_id=d AND status='PUBLISHED';
 IF source_rows IS NULL OR (catalog->0->>'row_count')::bigint
    IS DISTINCT FROM source_rows THEN
   RAISE EXCEPTION 'Analytics base row count differs';
 END IF;
 UPDATE dataset_gold_runs SET status='SUCCESS',completed_at=NOW(),
   failure_code=NULL,manifest_key=key,manifest_sha256=checksum,
   catalog=task21_analytics_publish.catalog,row_count=source_rows WHERE gold_run_id=runid;
 IF actual=r.source_state_id THEN
   UPDATE datasets SET current_gold_run_id=runid,state_analytics_status='FRESH'
   WHERE dataset_id=d;
 END IF;
END $$;
COMMIT;
