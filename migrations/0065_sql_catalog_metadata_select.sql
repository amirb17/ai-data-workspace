-- SQL catalog metadata reads for the existing authenticated API role.
-- DDL is intentionally limited to seven reviewed metadata tables.
-- No write privileges and no data/artifact table grants.
BEGIN;
GRANT SELECT ON TABLE
    public.semantic_profiles,
    public.semantic_column_evidence,
    public.semantic_suggestions,
    public.workspace_semantic_suggestions,
    public.relationship_discovery_runs,
    public.relationship_candidates,
    public.relationship_reviews
TO datarise_api;
COMMIT;
