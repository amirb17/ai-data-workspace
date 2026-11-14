-- Authorize a *first* APPEND load policy for an already verified Silver delivery.
-- No direct DML/ownership privilege is added to the public API role.
-- This never applies rows or publishes state: a new bounded worker job is required.
BEGIN;

CREATE FUNCTION task20_initial_append_policy(actor BIGINT, upload BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    src RECORD;
    a RECORD;
    existing RECORD;
    columns JSONB;
    profile_count BIGINT;
    policy BIGINT;
    state_id BIGINT;
BEGIN
    IF actor IS NULL OR upload IS NULL OR actor <= 0 OR upload <= 0 THEN
        RAISE EXCEPTION 'Invalid initial policy scope' USING ERRCODE='42501';
    END IF;

    SELECT * INTO src FROM task20_source(actor, upload);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Initial policy source unavailable' USING ERRCODE='42501';
    END IF;

    -- Lock the dataset once, before inspecting policies, to serialize first
    -- policy creation with other state/policy writers.
    SELECT current_state_id INTO state_id
    FROM datasets WHERE dataset_id=src.dataset_id FOR UPDATE;
    IF NOT FOUND OR state_id IS NOT NULL THEN
        RAISE EXCEPTION 'Initial policy requires an empty accepted dataset';
    END IF;

    SELECT f.dataset_version_file_id, f.dataset_version_id,
           f.applied_rule_version, dv.schema_hash
    INTO a
    FROM dataset_version_files f
    JOIN dataset_versions dv ON dv.dataset_version_id=f.dataset_version_id
    JOIN physical_files pf ON pf.file_id=f.file_id AND pf.schema_hash=dv.schema_hash
    JOIN approved_rule_policies rules
      ON rules.dataset_version_id=f.dataset_version_id
     AND rules.rule_version=f.applied_rule_version
    WHERE f.file_id=src.file_id AND dv.dataset_id=src.dataset_id
      AND f.applied_rule_version IS NOT NULL
      AND jsonb_typeof(rules.rules)='array'
      AND jsonb_array_length(rules.rules)>0
      AND NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(rules.rules) AS r
        WHERE r->'rule_config'->>'mode'='EXACT'
      )
    FOR SHARE OF f,dv,pf,rules;

    IF NOT FOUND THEN RAISE EXCEPTION 'Approved Silver contract required'; END IF;

    IF NOT EXISTS (
        SELECT 1 FROM processing_stage_heads h
        JOIN processing_artifact_candidates c ON c.candidate_id=h.candidate_id
        WHERE h.file_id=src.file_id AND h.stage='SILVER'
          AND h.dataset_version_file_id=a.dataset_version_file_id
          AND c.status='VERIFIED' AND c.verification_report IS NOT NULL
    ) OR NOT EXISTS (
        SELECT 1 FROM data_quality_runs dq
        JOIN processing_attempts pa ON pa.attempt_id=dq.attempt_id
        WHERE dq.dataset_version_file_id=a.dataset_version_file_id
          AND dq.rule_version=a.applied_rule_version
          AND dq.silver_path IS NOT NULL AND pa.status='SUCCESS'
    ) THEN RAISE EXCEPTION 'Verified replayable Silver required'; END IF;

    SELECT count(*),
           jsonb_agg(jsonb_build_object(
               'name', column_name,
               'data_type',
               CASE
                 WHEN lower(inferred_type) LIKE '%int%' THEN 'INTEGER'
                 WHEN lower(inferred_type) LIKE '%float%' THEN 'DECIMAL'
                 WHEN lower(inferred_type) LIKE '%bool%' THEN 'BOOLEAN'
                 WHEN lower(inferred_type) LIKE '%date%' THEN 'DATETIME'
                 ELSE 'TEXT'
               END
           ) ORDER BY column_name)
    INTO profile_count, columns
    FROM dataset_profiles WHERE file_id=src.file_id;

    IF profile_count < 1 OR profile_count > 128
      OR (SELECT count(DISTINCT lower(column_name))
          FROM dataset_profiles WHERE file_id=src.file_id) <> profile_count
      OR EXISTS (
        SELECT 1 FROM dataset_profiles WHERE file_id=src.file_id
          AND (column_name IS NULL OR btrim(column_name)=''
            OR column_name LIKE '\\_source\\_%' ESCAPE '\\'
            OR column_name LIKE '\\_datarise\\_%' ESCAPE '\\'
            OR column_name LIKE '\\_dq\\_%' ESCAPE '\\')
      )
    THEN RAISE EXCEPTION 'Unsafe or ambiguous profiled schema'; END IF;

    SELECT p.policy_id,p.dataset_version_id,p.load_strategy,
           p.schema_hash,p.schema_columns,p.business_keys,
           p.schema_evolution_policy,p.policy_version,p.event_time_column
    INTO existing
    FROM dataset_load_policies p WHERE p.dataset_id=src.dataset_id
    ORDER BY p.policy_version DESC LIMIT 1 FOR SHARE;

    IF FOUND THEN
       IF existing.policy_version<>1
          OR existing.dataset_version_id<>a.dataset_version_id
          OR existing.load_strategy<>'APPEND'
          OR existing.schema_hash<>a.schema_hash
          OR existing.schema_columns IS DISTINCT FROM columns
          OR existing.business_keys<>'[]'::jsonb
          OR existing.schema_evolution_policy<>'STRICT'
          OR existing.event_time_column IS NOT NULL
          OR (SELECT count(*) FROM dataset_load_policies WHERE dataset_id=src.dataset_id)<>1
       THEN RAISE EXCEPTION 'Existing load policy differs; no implicit replacement'; END IF;
       RETURN existing.policy_id;
    END IF;

    IF EXISTS (SELECT 1 FROM delivery_applications WHERE dataset_id=src.dataset_id)
       OR EXISTS (SELECT 1 FROM dataset_state_versions WHERE dataset_id=src.dataset_id) THEN
       RAISE EXCEPTION 'Application history already exists';
    END IF;

    INSERT INTO dataset_load_policies (
        dataset_id,dataset_version_id,policy_version,load_strategy,
        business_keys,schema_evolution_policy,event_time_column,
        schema_hash,schema_columns,created_by
    ) VALUES (
        src.dataset_id,a.dataset_version_id,1,'APPEND',
        '[]'::jsonb,'STRICT',NULL,a.schema_hash,columns,actor
    ) RETURNING policy_id INTO policy;
    RETURN policy;
END $$;

REVOKE ALL ON FUNCTION task20_initial_append_policy(BIGINT,BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION task20_initial_append_policy(BIGINT,BIGINT) TO datarise_api;
COMMIT;
