-- Ask AI execution permissions for the dedicated API role.
-- Apply once using a reviewed migration owner; never grant UPDATE or DELETE.
-- Enforce role/table presence rather than silently skipping an incomplete release.
BEGIN;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='datarise_api') THEN
    RAISE EXCEPTION 'Required API role datarise_api does not exist';
  END IF;
  IF to_regclass('public.workspace_answers') IS NULL THEN
    RAISE EXCEPTION 'Migration 0024 workspace_answers must be applied first';
  END IF;
  IF pg_get_serial_sequence('public.workspace_answers','answer_id') IS NULL THEN
    RAISE EXCEPTION 'workspace_answers.answer_id sequence is missing';
  END IF;
END $$;
GRANT SELECT ON TABLE public.workspace_answers TO datarise_api;
GRANT INSERT (
  workspace_id, previous_answer_id, question_hash, planner_key, cache_key,
  algorithm_version, payload, internal
) ON TABLE public.workspace_answers TO datarise_api;
GRANT USAGE ON SEQUENCE public.workspace_answers_answer_id_seq TO datarise_api;
DO $$
BEGIN
  IF NOT has_table_privilege('datarise_api','public.workspace_answers','SELECT')
     OR NOT (
       SELECT bool_and(has_column_privilege('datarise_api','public.workspace_answers',col,'INSERT'))
       FROM unnest(ARRAY[
         'workspace_id','previous_answer_id','question_hash','planner_key','cache_key',
         'algorithm_version','payload','internal'
       ]) AS col
     )
     OR NOT has_sequence_privilege('datarise_api','public.workspace_answers_answer_id_seq','USAGE')
     OR has_table_privilege('datarise_api','public.workspace_answers','UPDATE')
     OR has_table_privilege('datarise_api','public.workspace_answers','DELETE') THEN
    RAISE EXCEPTION 'Ask AI API role permission verification failed';
  END IF;
END $$;
COMMIT;
