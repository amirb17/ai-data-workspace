-- REVIEW ONLY. Privileged deployment requires separate operator approval.
-- No raw job table SELECT, job mutation, dispatch, or worker authority.
BEGIN;
-- Reuse the established Task 20 command owner; never leave a new DEFINER
-- function owned by the migration administrator or grant owner membership.
DO $$
DECLARE r pg_roles%ROWTYPE;
BEGIN
 SELECT * INTO r FROM pg_roles WHERE rolname='datarise_authority_owner';
 IF NOT FOUND OR r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
    OR r.rolreplication OR r.rolbypassrls OR r.rolinherit
 THEN RAISE EXCEPTION 'Reviewed Task 20 command owner required'; END IF;
 IF pg_has_role('datarise_api','datarise_authority_owner','MEMBER')
 THEN RAISE EXCEPTION 'API must not inherit command ownership'; END IF;
END $$;
CREATE FUNCTION public.task24_processing_jobs(actor BIGINT, ws BIGINT, ds BIGINT)
RETURNS TABLE(job_id UUID, upload_id BIGINT, operation VARCHAR,
              status VARCHAR, attempt_count INTEGER)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM public.users u
   JOIN public.workspaces w ON w.owner=u.owner_key
   JOIN public.datasets d ON d.workspace_id=w.workspace_id AND d.owner=u.owner_key
   WHERE u.user_id=actor AND u.account_status='ACTIVE'
     AND w.workspace_id=ws AND w.status='ACTIVE'
     AND d.dataset_id=ds AND d.status='ACTIVE'
 ) THEN RAISE EXCEPTION 'Processing scope unavailable' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT j.job_id,j.upload_id,j.operation,j.status,j.attempt_count
 FROM public.processing_jobs j
 JOIN public.upload_requests r ON r.upload_id=j.upload_id
   AND r.workspace_id=ws AND r.dataset_id=ds AND r.archived_at IS NULL
 WHERE j.workspace_id=ws AND j.dataset_id=ds
 ORDER BY j.created_at DESC,j.job_id DESC LIMIT 200;
END $$;
REVOKE ALL ON FUNCTION public.task24_processing_jobs(BIGINT,BIGINT,BIGINT) FROM PUBLIC;
ALTER FUNCTION public.task24_processing_jobs(BIGINT,BIGINT,BIGINT) OWNER TO datarise_authority_owner;
GRANT EXECUTE ON FUNCTION public.task24_processing_jobs(BIGINT,BIGINT,BIGINT) TO datarise_api;
COMMIT;
