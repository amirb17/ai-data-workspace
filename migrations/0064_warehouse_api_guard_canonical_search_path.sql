-- 0064: Repair immutable API admission trigger's canonical auth-table resolution.
-- 0061 accidentally omitted public from the trigger's stored search_path.
-- The trigger MUST retain its user/session/workspace checks, but the table
-- references live in public while the attempt registry lives in this schema.
BEGIN;
DO $preflight$
DECLARE
    active_schema text := current_schema();
BEGIN
    IF active_schema IS DISTINCT FROM 'datarise_warehouse_dev' THEN
        RAISE EXCEPTION '0064 requires the isolated warehouse query schema';
    END IF;
    IF to_regprocedure('datarise_warehouse_dev.warehouse_query_api_request_guard()') IS NULL
       OR to_regclass('datarise_warehouse_dev.warehouse_query_api_requests') IS NULL
       OR to_regclass('public.users') IS NULL
       OR to_regclass('public.auth_sessions') IS NULL
       OR to_regclass('public.workspaces') IS NULL THEN
        RAISE EXCEPTION '0061 API admission dependencies are incomplete';
    END IF;
END $preflight$;

-- No CREATE/REPLACE of the trigger function and no security definer; its
-- existing executable body and the immutable-row protection are unchanged.
ALTER FUNCTION datarise_warehouse_dev.warehouse_query_api_request_guard()
    SET search_path = pg_catalog, datarise_warehouse_dev, public, pg_temp;
REVOKE ALL ON FUNCTION datarise_warehouse_dev.warehouse_query_api_request_guard() FROM PUBLIC;
COMMIT;
