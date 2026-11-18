-- Allow the public API to persist immutable, owner-scoped Ask AI answers.
-- The service inserts one answer record for each completed question attempt.
-- It does not update or delete historical answers.
-- Applying this migration is idempotent and does not change existing data.
BEGIN;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datarise_api') THEN
    RAISE EXCEPTION 'Required role datarise_api is missing';
  END IF;
  IF to_regclass('public.workspace_answers') IS NULL THEN
    RAISE EXCEPTION 'Required workspace_answers table is missing';
  END IF;
  GRANT INSERT ON TABLE public.workspace_answers TO datarise_api;
  IF NOT has_table_privilege('datarise_api', 'public.workspace_answers', 'INSERT') THEN
    RAISE EXCEPTION 'Ask AI workspace_answers INSERT privilege still missing';
  END IF;
END $$;
COMMIT;
