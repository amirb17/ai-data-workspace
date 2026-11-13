-- Read-only capability and recent processing-job metadata for public GETs.
-- Do not enable dispatch, publish, job writes or warehouse execution.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
  IF current_database() <> 'aiworkspace' OR current_user <> 'workspace_admin' THEN
    RAISE EXCEPTION 'Wrong database or administrator';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datarise_api') THEN
    RAISE EXCEPTION 'API role is unavailable';
  END IF;
END $$;
GRANT SELECT ON TABLE
  public.warehouse_model_configurations,
  public.warehouse_models,
  public.warehouse_model_versions,
  public.warehouse_model_sources,
  public.processing_jobs
TO datarise_api;
COMMIT;
