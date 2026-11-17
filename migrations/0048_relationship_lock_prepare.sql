-- Stage A. Install the relationship-only lock capability before deploying
-- the API revision that calls it. No existing column UPDATE grants are revoked.
-- Apply as the owner of workspaces/datasets with the datarise_api role present.
BEGIN;

CREATE OR REPLACE FUNCTION public.lock_relationship_sources(
    p_workspace_id BIGINT,
    p_owner_key TEXT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    locked_dataset BIGINT;
BEGIN
    -- Do not return workspace/dataset content. The authenticated API supplies
    -- the owner_key, which is also revalidated in the application transaction.
    IF p_workspace_id IS NULL OR p_workspace_id <= 0
       OR p_owner_key IS NULL OR length(p_owner_key) = 0 THEN
        RAISE EXCEPTION 'Relationship scope unavailable' USING ERRCODE = '42501';
    END IF;

    -- Preserve the existing parent-then-child FOR UPDATE locking order.
    -- PostgreSQL retains both locks until the caller commits/rolls back.
    PERFORM 1
    FROM public.workspaces AS w
    WHERE w.workspace_id = p_workspace_id
      AND w.owner = p_owner_key
      AND w.status = 'ACTIVE'
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Relationship scope unavailable' USING ERRCODE = '42501';
    END IF;

    FOR locked_dataset IN
        SELECT d.dataset_id
        FROM public.datasets AS d
        WHERE d.workspace_id = p_workspace_id
        ORDER BY d.dataset_id
        FOR UPDATE
    LOOP
        NULL;
    END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION public.lock_relationship_sources(BIGINT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.lock_relationship_sources(BIGINT, TEXT) TO datarise_api;

-- Persist only relationship-specific evidence/reviews, not source data.
GRANT SELECT, INSERT ON TABLE
    public.relationship_discovery_runs,
    public.relationship_candidates,
    public.relationship_reviews TO datarise_api;
GRANT UPDATE (status, failure_code, completed_at, candidate_count, suppressed_composite_pairs)
    ON TABLE public.relationship_discovery_runs TO datarise_api;
GRANT USAGE ON SEQUENCE
    public.relationship_discovery_runs_run_id_seq,
    public.relationship_candidates_candidate_id_seq,
    public.relationship_reviews_review_id_seq TO datarise_api;

COMMIT;
