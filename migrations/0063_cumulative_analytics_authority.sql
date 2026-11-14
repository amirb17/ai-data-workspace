-- Separately reviewed authority for cumulative Gold. The API never receives
-- INSERT/UPDATE on datasets or dataset_gold_runs; its authenticated user ID
-- is checked against the current workspace/dataset owner on every transition.
BEGIN;

CREATE FUNCTION task21_analytics_owner(actor BIGINT, w BIGINT, d BIGINT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM users u
   JOIN workspaces ws ON ws.owner=u.owner_key
   JOIN datasets ds ON ds.workspace_id=ws.workspace_id AND ds.owner=u.owner_key
   WHERE u.user_id=actor AND u.account_status='ACTIVE'
     AND ws.workspace_id=w AND ws.status='ACTIVE'
     AND ds.dataset_id=d AND ds.status<>'ARCHIVED'
 ) THEN
   RAISE EXCEPTION 'Analytics owner unavailable' USING ERRCODE='42501';
 END IF;
END $$;

CREATE FUNCTION task21_analytics_begin(actor BIGINT,w BIGINT,d BIGINT,expected_state BIGINT)
RETURNS TABLE(run_id BIGINT,reused BOOLEAN) LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE s dataset_state_versions; old_run dataset_gold_runs; actual BIGINT;
BEGIN
 PERFORM task21_analytics_owner(actor,w,d);
 SELECT current_state_id INTO actual FROM datasets
 WHERE dataset_id=d AND workspace_id=w FOR UPDATE;
 IF actual IS DISTINCT FROM expected_state OR actual IS NULL THEN
   RAISE EXCEPTION 'Current analytics source changed';
 END IF;
 SELECT * INTO s FROM dataset_state_versions
 WHERE state_id=actual AND dataset_id=d AND status='PUBLISHED';
 IF NOT FOUND THEN RAISE EXCEPTION 'Published analytics source required'; END IF;
 SELECT * INTO old_run FROM dataset_gold_runs
 WHERE dataset_id=d AND source_state_id=actual AND build_version=1 FOR UPDATE;
 IF FOUND AND old_run.status='SUCCESS' THEN
   UPDATE datasets SET current_gold_run_id=old_run.gold_run_id,
      state_analytics_status='FRESH' WHERE dataset_id=d;
   RETURN QUERY SELECT old_run.gold_run_id,TRUE;
   RETURN;
 END IF;
 IF old_run.gold_run_id IS NULL THEN
   INSERT INTO dataset_gold_runs(dataset_id,source_state_id,dataset_version_id,
     policy_id,source_sha256,status)
   VALUES(d,s.state_id,s.dataset_version_id,s.policy_id,s.manifest_sha256,'REFRESHING')
   RETURNING * INTO old_run;
 ELSE
   UPDATE dataset_gold_runs SET status='REFRESHING',started_at=NOW(),
     completed_at=NULL,failure_code=NULL WHERE gold_run_id=old_run.gold_run_id;
 END IF;
 UPDATE datasets SET state_analytics_status='REFRESHING' WHERE dataset_id=d;
 RETURN QUERY SELECT old_run.gold_run_id,FALSE;
END $$;

CREATE FUNCTION task21_analytics_publish(actor BIGINT,w BIGINT,d BIGINT,
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
    OR catalog->0->>'artifact_type' <> 'BASE'
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
   catalog=catalog,row_count=source_rows WHERE gold_run_id=runid;
 IF actual=r.source_state_id THEN
   UPDATE datasets SET current_gold_run_id=runid,state_analytics_status='FRESH'
   WHERE dataset_id=d;
 END IF;
END $$;

CREATE FUNCTION task21_analytics_fail(actor BIGINT,w BIGINT,d BIGINT,
  runid BIGINT,source_state BIGINT,reason TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE actual BIGINT; r dataset_gold_runs;
BEGIN
 PERFORM task21_analytics_owner(actor,w,d);
 IF reason NOT IN ('BUILD_FAILED','VALIDATION_FAILED') THEN
   RAISE EXCEPTION 'Unapproved analytics failure category';
 END IF;
 SELECT current_state_id INTO actual FROM datasets
 WHERE dataset_id=d AND workspace_id=w FOR UPDATE;
 SELECT * INTO r FROM dataset_gold_runs
 WHERE gold_run_id=runid AND dataset_id=d AND source_state_id=source_state FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Analytics run unavailable'; END IF;
 IF r.status='SUCCESS' THEN RETURN; END IF;
 UPDATE dataset_gold_runs SET status='FAILED',completed_at=NOW(),failure_code=reason
 WHERE gold_run_id=runid;
 IF actual=source_state THEN
   UPDATE datasets SET state_analytics_status='FAILED' WHERE dataset_id=d;
 END IF;
END $$;

REVOKE ALL ON FUNCTION task21_analytics_owner(BIGINT,BIGINT,BIGINT),
 task21_analytics_begin(BIGINT,BIGINT,BIGINT,BIGINT),
 task21_analytics_publish(BIGINT,BIGINT,BIGINT,BIGINT,TEXT,TEXT,JSONB),
 task21_analytics_fail(BIGINT,BIGINT,BIGINT,BIGINT,TEXT)
 FROM PUBLIC;
GRANT EXECUTE ON FUNCTION task21_analytics_begin(BIGINT,BIGINT,BIGINT,BIGINT),
 task21_analytics_publish(BIGINT,BIGINT,BIGINT,BIGINT,TEXT,TEXT,JSONB),
 task21_analytics_fail(BIGINT,BIGINT,BIGINT,BIGINT,TEXT)
 TO datarise_api;
COMMIT;
