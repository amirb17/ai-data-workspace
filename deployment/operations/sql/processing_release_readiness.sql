-- READ ONLY: AWS processing production preflight for aiworkspace.
-- Run over the private RDS SSM tunnel with the approved administrator.
-- Displays presence/privilege booleans and counts only. Never emits secrets,
-- owner identities, raw paths, job tokens or protected source bytes.
\set ON_ERROR_STOP on
BEGIN READ ONLY;
SELECT current_database() AS database,
       current_user AS inspected_as,
       current_setting('transaction_read_only') AS read_only;
SELECT r.rolname AS runtime_role
FROM pg_roles r
WHERE r.rolname IN ('datarise_api','datarise_worker','datarise_dispatch','datarise_verifier')
ORDER BY r.rolname;
SELECT capability, present
FROM (VALUES
  ('source_registry', to_regclass('public.processing_source_objects') IS NOT NULL),
  ('processing_jobs', to_regclass('public.processing_jobs') IS NOT NULL),
  ('stage_attempts', to_regclass('public.processing_job_stage_attempts') IS NOT NULL),
  ('stage_intents', to_regclass('public.processing_stage_intents') IS NOT NULL),
  ('artifact_candidates', to_regclass('public.processing_artifact_candidates') IS NOT NULL),
  ('stage_receipts', to_regclass('public.processing_stage_receipts') IS NOT NULL),
  ('stage_heads', to_regclass('public.processing_stage_heads') IS NOT NULL),
  ('dispatch_outbox', to_regclass('public.processing_dispatch_outbox') IS NOT NULL),
  ('admission_pins', to_regclass('public.processing_admission_pins') IS NOT NULL),
  ('admit_rpc', to_regprocedure('public.task20_admit(bigint,bigint,text,text)') IS NOT NULL),
  ('register_source_rpc', to_regprocedure('public.task20_register_source(bigint,bigint,jsonb)') IS NOT NULL),
  ('publish_rpc', to_regprocedure('public.task20_publish(uuid,uuid,uuid)') IS NOT NULL),
  ('verify_rpc', to_regprocedure('public.task20_verify(uuid,uuid,uuid,jsonb)') IS NOT NULL),
  ('claim_dispatch_rpc', to_regprocedure('public.task20_claim_dispatch()') IS NOT NULL)
) AS readiness(capability,present) ORDER BY capability;

-- Roles have distinct duties; verify the restricted grants before activation.
SELECT role_name, exists_in_db,
 CASE WHEN exists_in_db THEN has_function_privilege(role_name, 'public.task20_admit(bigint,bigint,text,text)','EXECUTE') ELSE false END AS may_admit,
 CASE WHEN exists_in_db THEN has_function_privilege(role_name, 'public.task20_claim_dispatch()','EXECUTE') ELSE false END AS may_dispatch,
 CASE WHEN exists_in_db THEN has_function_privilege(role_name, 'public.task20_publish(uuid,uuid,uuid)','EXECUTE') ELSE false END AS may_publish,
 CASE WHEN exists_in_db THEN has_function_privilege(role_name, 'public.task20_verify(uuid,uuid,uuid,jsonb)','EXECUTE') ELSE false END AS may_verify
FROM (SELECT v.role_name, EXISTS(SELECT 1 FROM pg_roles WHERE rolname=v.role_name) AS exists_in_db
      FROM (VALUES('datarise_api'),('datarise_worker'),('datarise_dispatch'),('datarise_verifier')) AS v(role_name)) AS x;

DO $$
DECLARE total_uploads integer; pinned_uploads integer; unpinned_uploads integer;
BEGIN
 IF to_regclass('public.processing_source_objects') IS NULL THEN
   RAISE NOTICE 'SOURCE_PIN_STATUS: registry missing; stage admission NOT READY';
 ELSE
   EXECUTE $query$
     SELECT count(*)::integer,
            count(*) FILTER(WHERE s.file_id IS NOT NULL)::integer,
            count(*) FILTER(WHERE s.file_id IS NULL)::integer
     FROM public.upload_requests u
     JOIN public.physical_files f USING(file_id)
     LEFT JOIN public.processing_source_objects s USING(file_id)
     WHERE u.workspace_id=1 AND u.dataset_id=1 AND u.status='UPLOADED'
       AND u.archived_at IS NULL
   $query$ INTO total_uploads,pinned_uploads,unpinned_uploads;
   RAISE NOTICE 'WORKSPACE_1_DATASET_1: uploaded=%, version_pinned=%, unpinned=%',
     total_uploads,pinned_uploads,unpinned_uploads;
 END IF;
END $$;
ROLLBACK;
