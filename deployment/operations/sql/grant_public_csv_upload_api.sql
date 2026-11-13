-- Restricted upload-session SQL privileges for the approved public CSV workflow.
-- Apply as workspace_admin to the private aiworkspace database over SSM.
-- Do not grant write privileges to users, workspaces, datasets or processing.
\set ON_ERROR_STOP on
BEGIN;
DO $$
DECLARE seq text;
BEGIN
  IF current_database() <> 'aiworkspace' OR current_user <> 'workspace_admin' THEN
    RAISE EXCEPTION 'Wrong database or admin role';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='datarise_api') THEN
    RAISE EXCEPTION 'API role not found';
  END IF;
  IF to_regclass('public.upload_requests') IS NULL OR to_regclass('public.physical_files') IS NULL THEN
    RAISE EXCEPTION 'Upload relations missing';
  END IF;
  FOREACH seq IN ARRAY ARRAY[
    pg_get_serial_sequence('public.upload_requests','upload_id'),
    pg_get_serial_sequence('public.physical_files','file_id')
  ] LOOP
    IF seq IS NULL THEN RAISE EXCEPTION 'Upload primary-key sequence missing'; END IF;
    EXECUTE format('GRANT USAGE ON SEQUENCE %s TO datarise_api',seq);
  END LOOP;
END $$;
-- Required by INSERT and SELECT ... FOR UPDATE in upload_session_repository.
GRANT SELECT, INSERT, UPDATE ON TABLE public.upload_requests TO datarise_api;
-- Required by checksum deduplication and creating the immutable physical file.
GRANT SELECT, INSERT ON TABLE public.physical_files TO datarise_api;
COMMIT;
