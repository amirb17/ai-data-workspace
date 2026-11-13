-- Reviewed, explicit authentication runtime grants for the DataRise ECS API.
-- Connect to the private aiworkspace DB as workspace_admin (RDS master user).
-- Does not create roles, alter ownership, grant DDL or grant on warehouse tables.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
 IF current_database() <> 'aiworkspace' THEN
   RAISE EXCEPTION 'Wrong database: %', current_database();
 END IF;
 IF current_user <> 'workspace_admin' THEN
   RAISE EXCEPTION 'Run with approved workspace_admin role, got %', current_user;
 END IF;
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='datarise_api') THEN
   RAISE EXCEPTION 'API role missing';
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO datarise_api;
GRANT SELECT, INSERT, UPDATE ON TABLE
 public.users,
 public.auth_rate_limits,
 public.auth_challenges,
 public.auth_sessions,
 public.auth_oauth_states
 TO datarise_api;
GRANT SELECT, INSERT ON TABLE public.auth_identities TO datarise_api;
GRANT INSERT ON TABLE public.auth_security_events TO datarise_api;
-- Use default BIGSERIAL sequences; no ALL SEQUENCES grant.
GRANT USAGE ON SEQUENCE
 public.users_user_id_seq,
 public.auth_identities_identity_id_seq,
 public.auth_security_events_event_id_seq
 TO datarise_api;
COMMIT;
