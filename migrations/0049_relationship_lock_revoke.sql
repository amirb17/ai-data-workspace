-- Stage C. Apply ONLY AFTER the new API image has passed live discovery
-- and review with public.lock_relationship_sources(). The old image still
-- executes SELECT ... FOR UPDATE as datarise_api and requires these grants.
BEGIN;

REVOKE UPDATE (workspace_name) ON TABLE public.workspaces FROM datarise_api;
REVOKE UPDATE (dataset_name) ON TABLE public.datasets FROM datarise_api;

-- Fail closed if an inherited table/column grant still permits these writes.
DO $$
BEGIN
    IF has_column_privilege('datarise_api', 'public.workspaces', 'workspace_name', 'UPDATE')
       OR has_column_privilege('datarise_api', 'public.datasets', 'dataset_name', 'UPDATE')
    THEN
        RAISE EXCEPTION 'Relationship API still has source name UPDATE privilege';
    END IF;
    IF NOT has_function_privilege('datarise_api',
           'public.lock_relationship_sources(bigint,text)', 'EXECUTE')
    THEN
        RAISE EXCEPTION 'Relationship API lost scoped lock EXECUTE';
    END IF;
END;
$$;

COMMIT;
