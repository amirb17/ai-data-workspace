-- Acquire the existing strong publication locks for Ask AI without granting
-- any direct UPDATE privilege on workspace or dataset records to datarise_api.
-- Ownership is checked inside the definer before locking child datasets.
BEGIN;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'datarise_api') THEN
    RAISE EXCEPTION 'Missing datarise_api role';
  END IF;
  IF to_regclass('public.workspaces') IS NULL OR to_regclass('public.datasets') IS NULL THEN
    RAISE EXCEPTION 'Missing workspace or dataset tables';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION public.ask_ai_lock_owned_workspace(
  p_workspace_id bigint,
  p_owner_key text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF p_workspace_id IS NULL OR p_workspace_id < 1 OR
     p_owner_key IS NULL OR length(p_owner_key) = 0 THEN
    RAISE EXCEPTION 'Workspace context invalid' USING ERRCODE = '42501';
  END IF;

  -- Parent first: serializes membership insertion and owner changes.
  PERFORM 1 FROM public.workspaces
    WHERE workspace_id = p_workspace_id AND owner = p_owner_key
      AND status = 'ACTIVE'
    FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Workspace unavailable' USING ERRCODE = '42501';
  END IF;

  -- Lock all membership rows in stable order. No data leaves this function.
  PERFORM dataset_id FROM public.datasets
    WHERE workspace_id = p_workspace_id
    ORDER BY dataset_id
    FOR UPDATE;
END;
$$;

REVOKE ALL ON FUNCTION public.ask_ai_lock_owned_workspace(bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ask_ai_lock_owned_workspace(bigint,text) TO datarise_api;

DO $$
BEGIN
 IF NOT has_function_privilege(
   'datarise_api', 'public.ask_ai_lock_owned_workspace(bigint,text)', 'EXECUTE'
 ) THEN
   RAISE EXCEPTION 'Ask AI lock function EXECUTE permission missing';
 END IF;
END $$;
COMMIT;
