-- Approved read-only public dataset metadata for DataRise API.
-- Run on private aiworkspace RDS as workspace_admin through an authorized SSM tunnel.
-- Only six explicitly identified tables; does not enable writes, ingestion,
-- processing, warehouse queries, new roles, ownership changes or broad schema ACLs.
\set ON_ERROR_STOP on
BEGIN;
DO $$
DECLARE missing text;
BEGIN
 IF current_database() <> 'aiworkspace' OR current_user <> 'workspace_admin' THEN
    RAISE EXCEPTION 'Wrong database or administrator role';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datarise_api') THEN
    RAISE EXCEPTION 'Expected datarise_api role missing';
 END IF;
 SELECT string_agg(t, ', ') INTO missing
 FROM unnest(ARRAY[
   'public.upload_requests', 'public.physical_files',
   'public.dataset_state_versions', 'public.delivery_applications',
   'public.dataset_load_policies', 'public.dataset_gold_runs'
 ]) AS t WHERE to_regclass(t) IS NULL;
 IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'Required relations missing: %', missing;
 END IF;
END $$;
GRANT SELECT ON TABLE
 public.upload_requests,
 public.physical_files,
 public.dataset_state_versions,
 public.delivery_applications,
 public.dataset_load_policies,
 public.dataset_gold_runs
TO datarise_api;
COMMIT;
