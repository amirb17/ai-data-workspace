-- Existing Ask AI reads profiles, metric evidence and immutable results.
-- No writes, no data mutation, no schema alteration, and no PUBLIC grants.
BEGIN;
DO $$
DECLARE
  tbl text;
  required text[] := ARRAY[
    'semantic_profiles',
    'semantic_column_evidence',
    'dataset_versions',
    'metric_discovery_runs',
    'metric_candidates',
    'metric_reviews',
    'approved_metric_definitions',
    'workspace_metric_results'
  ];
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = 'datarise_api') THEN
    RAISE EXCEPTION 'datarise_api role is missing';
  END IF;
  FOREACH tbl IN ARRAY required LOOP
    IF to_regclass('public.' || tbl) IS NULL THEN
      RAISE EXCEPTION 'Required Ask AI read table missing: %', tbl;
    END IF;
    EXECUTE format('GRANT SELECT ON TABLE public.%I TO datarise_api', tbl);
    IF NOT has_table_privilege('datarise_api', format('public.%I',tbl), 'SELECT') THEN
      RAISE EXCEPTION 'Ask AI SELECT privilege missing on %', tbl;
    END IF;
  END LOOP;
END $$;
COMMIT;
