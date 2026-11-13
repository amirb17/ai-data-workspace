-- DataRise workspace/dataset creation only. Explicitly reviewed public opt-in.
-- Run as workspace_admin on the private RDS aiworkspace DB over SSM.
-- No privilege changes for S3, processing, AI, warehouse or global tables.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
 IF current_database() <> 'aiworkspace' OR current_user <> 'workspace_admin' THEN
   RAISE EXCEPTION 'Wrong admin connection';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='datarise_api') THEN
   RAISE EXCEPTION 'API role missing';
 END IF;
END $$;
-- SELECT was previously granted for the public dashboard.
GRANT INSERT ON TABLE public.workspaces, public.datasets TO datarise_api;
GRANT USAGE ON SEQUENCE public.workspaces_workspace_id_seq,
                        public.datasets_dataset_id_seq TO datarise_api;
COMMIT;
