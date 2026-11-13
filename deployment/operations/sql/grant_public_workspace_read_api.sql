-- AWS DataRise public read-only workspace navigation grants.
-- Run as workspace_admin on private RDS aiworkspace via the approved SSM tunnel.
-- Only SELECT on workspaces and datasets; no workspace/dataset creation privileges.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
 IF current_database() <> 'aiworkspace' THEN
   RAISE EXCEPTION 'Wrong database %', current_database();
 END IF;
 IF current_user <> 'workspace_admin' THEN
   RAISE EXCEPTION 'Expected workspace_admin, got %', current_user;
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datarise_api') THEN
   RAISE EXCEPTION 'Expected datarise_api role';
 END IF;
 IF to_regclass('public.workspaces') IS NULL OR to_regclass('public.datasets') IS NULL THEN
   RAISE EXCEPTION 'Expected public workspace and dataset tables';
 END IF;
END $$;
GRANT SELECT ON TABLE public.workspaces, public.datasets TO datarise_api;
COMMIT;
-- Execute a new connection as datarise_api to confirm effective access.
