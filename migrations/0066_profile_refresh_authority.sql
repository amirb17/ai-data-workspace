-- API profile publication via owner-fenced SECURITY DEFINER authority.
-- No direct INSERT/UPDATE/DELETE grants on profile or dataset tables.
BEGIN;

CREATE FUNCTION task22_profile_begin(actor BIGINT,w BIGINT,d BIGINT,
 expected_state BIGINT, algorithm INTEGER)
RETURNS TABLE(profile_id BIGINT,reused BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE s dataset_state_versions; p semantic_profiles; current_head BIGINT; v INTEGER;
BEGIN
 PERFORM task21_analytics_owner(actor,w,d);
 SELECT current_state_id INTO current_head FROM datasets
 WHERE workspace_id=w AND dataset_id=d FOR UPDATE;
 IF current_head IS NULL OR current_head IS DISTINCT FROM expected_state
 THEN RAISE EXCEPTION 'Profile source changed'; END IF;
 SELECT * INTO s FROM dataset_state_versions
 WHERE state_id=current_head AND dataset_id=d AND status='PUBLISHED';
 IF NOT FOUND THEN RAISE EXCEPTION 'Profile requires published state'; END IF;
 SELECT * INTO p FROM semantic_profiles
 WHERE dataset_id=d AND source_state_id=s.state_id AND algorithm_version=algorithm
 FOR UPDATE;
 IF FOUND AND p.status='READY' THEN
   UPDATE datasets SET current_profile_id=p.profile_id,profile_status='READY'
   WHERE dataset_id=d;
   RETURN QUERY SELECT p.profile_id,TRUE;
   RETURN;
 END IF;
 IF p.profile_id IS NULL THEN
   SELECT COALESCE(MAX(profile_version),0)+1 INTO v
   FROM semantic_profiles WHERE dataset_id=d;
   INSERT INTO semantic_profiles(owner_key,workspace_id,dataset_id,source_state_id,
     dataset_version_id,policy_id,source_sha256,profile_version,algorithm_version,status)
   SELECT ds.owner,w,d,s.state_id,s.dataset_version_id,s.policy_id,s.manifest_sha256,v,algorithm,'PROFILING'
   FROM datasets ds WHERE ds.dataset_id=d RETURNING semantic_profiles.profile_id INTO p.profile_id;
 ELSE
   UPDATE semantic_profiles SET status='PROFILING',completed_at=NULL,failure_code=NULL
   WHERE semantic_profiles.profile_id=p.profile_id;
 END IF;
 UPDATE datasets SET profile_status='PROFILING' WHERE dataset_id=d;
 RETURN QUERY SELECT p.profile_id,FALSE;
END $$;

CREATE FUNCTION task22_profile_publish(actor BIGINT,w BIGINT,d BIGINT,
 pid BIGINT,expected_state BIGINT,profile_summary JSONB,profile_columns JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE p semantic_profiles; head BIGINT; n INTEGER; item JSONB; i INTEGER:=0;
BEGIN
 PERFORM task21_analytics_owner(actor,w,d);
 SELECT current_state_id INTO head FROM datasets
 WHERE dataset_id=d AND workspace_id=w FOR UPDATE;
 SELECT * INTO p FROM semantic_profiles
 WHERE semantic_profiles.profile_id=pid AND dataset_id=d
 AND workspace_id=w AND source_state_id=expected_state FOR UPDATE;
 IF NOT FOUND OR p.status<>'PROFILING' THEN
   RAISE EXCEPTION 'Profile publication not authorized'; END IF;
 IF jsonb_typeof(profile_summary) IS DISTINCT FROM 'object'
    OR jsonb_typeof(profile_columns) IS DISTINCT FROM 'array'
    OR jsonb_array_length(profile_columns)<1
    OR (profile_summary->>'column_count')::int
       IS DISTINCT FROM jsonb_array_length(profile_columns)
    OR (profile_summary->>'row_count')::bigint
       IS DISTINCT FROM (SELECT row_count FROM dataset_state_versions
                         WHERE state_id=expected_state AND dataset_id=d)
 THEN RAISE EXCEPTION 'Profile evidence accounting invalid'; END IF;
 -- Confirm evidence columns match the pinned load policy, including ordering.
 SELECT jsonb_array_length(schema_columns) INTO n
 FROM dataset_load_policies WHERE policy_id=p.policy_id AND dataset_id=d;
 IF n IS DISTINCT FROM jsonb_array_length(profile_columns)
 THEN RAISE EXCEPTION 'Profile schema width mismatch'; END IF;
 DELETE FROM semantic_column_evidence WHERE semantic_column_evidence.profile_id=pid;
 FOR item IN SELECT value FROM jsonb_array_elements(profile_columns)
 LOOP
    i:=i+1;
    IF jsonb_typeof(item) IS DISTINCT FROM 'object'
       OR item->>'original_name' IS DISTINCT FROM (
         SELECT schema_columns->(i-1)->>'name'
         FROM dataset_load_policies WHERE policy_id=p.policy_id AND dataset_id=d
       )
    THEN RAISE EXCEPTION 'Profile evidence column mismatch'; END IF;
    INSERT INTO semantic_column_evidence(profile_id,ordinal_position,column_name,evidence)
    VALUES(pid,i,item->>'original_name',item);
 END LOOP;
 UPDATE semantic_profiles SET status='READY',summary=profile_summary,
 completed_at=NOW(),failure_code=NULL WHERE semantic_profiles.profile_id=pid;
 IF head=expected_state THEN
   UPDATE datasets SET current_profile_id=pid,profile_status='READY' WHERE dataset_id=d;
 END IF;
END $$;

CREATE FUNCTION task22_profile_fail(actor BIGINT,w BIGINT,d BIGINT,
 pid BIGINT,expected_state BIGINT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE p semantic_profiles; head BIGINT;
BEGIN
 PERFORM task21_analytics_owner(actor,w,d);
 SELECT current_state_id INTO head FROM datasets
 WHERE workspace_id=w AND dataset_id=d FOR UPDATE;
 SELECT * INTO p FROM semantic_profiles
 WHERE semantic_profiles.profile_id=pid AND dataset_id=d
 AND workspace_id=w AND source_state_id=expected_state FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Profile run not found'; END IF;
 IF p.status='READY' THEN RETURN; END IF;
 UPDATE semantic_profiles SET status='FAILED',completed_at=NOW(),
   failure_code='PROFILE_FAILED' WHERE semantic_profiles.profile_id=pid;
 IF head=expected_state THEN
   UPDATE datasets SET profile_status='FAILED' WHERE dataset_id=d;
 END IF;
END $$;

REVOKE ALL ON FUNCTION
 task22_profile_begin(BIGINT,BIGINT,BIGINT,BIGINT,INTEGER),
 task22_profile_publish(BIGINT,BIGINT,BIGINT,BIGINT,BIGINT,JSONB,JSONB),
 task22_profile_fail(BIGINT,BIGINT,BIGINT,BIGINT,BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
 task22_profile_begin(BIGINT,BIGINT,BIGINT,BIGINT,INTEGER),
 task22_profile_publish(BIGINT,BIGINT,BIGINT,BIGINT,BIGINT,JSONB,JSONB),
 task22_profile_fail(BIGINT,BIGINT,BIGINT,BIGINT,BIGINT) TO datarise_api;
-- Already reviewed business-rule evidence; only SELECT needed.
GRANT SELECT ON TABLE public.approved_rule_policies TO datarise_api;
COMMIT;
