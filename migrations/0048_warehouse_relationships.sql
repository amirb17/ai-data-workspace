-- Additive, version-pinned FACT -> DIMENSION relationship registry.
-- This migration does not infer joins, update warehouse versions, or enable SQL.
BEGIN;

CREATE TABLE warehouse_model_relationships (
    relationship_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    fact_model_id BIGINT NOT NULL,
    fact_model_version_id BIGINT NOT NULL,
    dimension_model_id BIGINT NOT NULL,
    dimension_model_version_id BIGINT NOT NULL,
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120 AND name = btrim(name)),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
    cardinality TEXT NOT NULL CHECK (cardinality = 'MANY_TO_ONE'),
    temporal_policy TEXT NOT NULL CHECK (temporal_policy IN ('CURRENT_MEMBER', 'EVENT_TIME_AS_OF')),
    fact_event_time_column TEXT,
    key_pairs JSONB NOT NULL CHECK (
        jsonb_typeof(key_pairs) = 'array'
        AND jsonb_array_length(key_pairs) BETWEEN 1 AND 32
    ),
    reviewed_by BIGINT NOT NULL REFERENCES users(user_id),
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (workspace_id, fact_model_id, fact_model_version_id)
        REFERENCES warehouse_model_versions(workspace_id, warehouse_model_id, warehouse_model_version_id),
    FOREIGN KEY (workspace_id, dimension_model_id, dimension_model_version_id)
        REFERENCES warehouse_model_versions(workspace_id, warehouse_model_id, warehouse_model_version_id),
    CHECK (fact_model_id <> dimension_model_id),
    CHECK (
        (temporal_policy = 'CURRENT_MEMBER' AND fact_event_time_column IS NULL)
        OR
        (temporal_policy = 'EVENT_TIME_AS_OF' AND fact_event_time_column IS NOT NULL
            AND length(fact_event_time_column) BETWEEN 1 AND 255)
    ),
    UNIQUE (workspace_id, fact_model_version_id, dimension_model_version_id, name)
);

CREATE INDEX warehouse_relationships_fact
    ON warehouse_model_relationships(workspace_id, fact_model_version_id);
CREATE INDEX warehouse_relationships_dimension
    ON warehouse_model_relationships(workspace_id, dimension_model_version_id);

-- A relationship is a reviewed, immutable evidence record. A new relationship
-- must be inserted for changed model versions or changed join semantics.
CREATE FUNCTION warehouse_relationship_history_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Reviewed warehouse relationships are immutable';
END;
$$;

CREATE TRIGGER warehouse_relationship_no_update
    BEFORE UPDATE OR DELETE ON warehouse_model_relationships
    FOR EACH ROW EXECUTE FUNCTION warehouse_relationship_history_guard();

CREATE TRIGGER warehouse_relationship_no_truncate
    BEFORE TRUNCATE ON warehouse_model_relationships
    FOR EACH STATEMENT EXECUTE FUNCTION warehouse_no_truncate();

-- Database-enforced semantic validation, even when a privileged caller bypasses
-- the application service. No inference from similarly named columns.
CREATE FUNCTION warehouse_relationship_insert_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    fv warehouse_model_versions%ROWTYPE;
    dv warehouse_model_versions%ROWTYPE;
    fc warehouse_model_configurations%ROWTYPE;
    dc warehouse_model_configurations%ROWTYPE;
    p JSONB;
    pair_count INTEGER := 0;
    fact_names TEXT[] := ARRAY[]::TEXT[];
    dimension_names TEXT[] := ARRAY[]::TEXT[];
    fact_name TEXT;
    dimension_name TEXT;
    fact_type TEXT;
    dimension_type TEXT;
    fact_identity JSONB;
    fact_attrs JSONB;
    dim_business_keys JSONB;
    dim_key TEXT;
BEGIN
    SELECT * INTO fv FROM warehouse_model_versions
      WHERE workspace_id = NEW.workspace_id
        AND warehouse_model_id = NEW.fact_model_id
        AND warehouse_model_version_id = NEW.fact_model_version_id
      FOR SHARE;
    SELECT * INTO dv FROM warehouse_model_versions
      WHERE workspace_id = NEW.workspace_id
        AND warehouse_model_id = NEW.dimension_model_id
        AND warehouse_model_version_id = NEW.dimension_model_version_id
      FOR SHARE;

    IF NOT FOUND OR fv.status IS DISTINCT FROM 'APPROVED'
       OR dv.status IS DISTINCT FROM 'APPROVED' THEN
        RAISE EXCEPTION 'Both relationship model versions must be approved';
    END IF;

    SELECT * INTO fc FROM warehouse_model_configurations
      WHERE workspace_id = NEW.workspace_id
        AND warehouse_model_id = NEW.fact_model_id
        AND warehouse_model_version_id = NEW.fact_model_version_id
      FOR SHARE;
    SELECT * INTO dc FROM warehouse_model_configurations
      WHERE workspace_id = NEW.workspace_id
        AND warehouse_model_id = NEW.dimension_model_id
        AND warehouse_model_version_id = NEW.dimension_model_version_id
      FOR SHARE;

    IF fc.model_type IS DISTINCT FROM 'FACT'
       OR dc.model_type IS DISTINCT FROM 'DIMENSION' THEN
        RAISE EXCEPTION 'Relationship requires typed FACT and DIMENSION definitions';
    END IF;

    fact_identity := fc.configuration->'grain'->'identity_columns';
    fact_attrs := COALESCE(fc.configuration->'attributes', '[]'::jsonb);
    dim_business_keys := dc.configuration->'business_keys';

    IF jsonb_array_length(NEW.key_pairs) <> jsonb_array_length(dim_business_keys) THEN
        RAISE EXCEPTION 'All dimension business keys must be mapped';
    END IF;

    FOR p IN SELECT value FROM jsonb_array_elements(NEW.key_pairs)
    LOOP
        IF jsonb_typeof(p) IS DISTINCT FROM 'object'
           OR NOT p ?& ARRAY['fact_column', 'dimension_column']
           OR p - ARRAY['fact_column', 'dimension_column'] <> '{}'::jsonb
           OR jsonb_typeof(p->'fact_column') IS DISTINCT FROM 'string'
           OR jsonb_typeof(p->'dimension_column') IS DISTINCT FROM 'string'
        THEN
            RAISE EXCEPTION 'Invalid reviewed key mapping';
        END IF;

        fact_name := p->>'fact_column';
        dimension_name := p->>'dimension_column';

        IF fact_name = ANY(fact_names)
           OR dimension_name = ANY(dimension_names)
           OR NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements_text(fact_identity || fact_attrs) AS k(name)
               WHERE k.name = fact_name
           )
           OR NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements_text(dim_business_keys) AS k(name)
               WHERE k.name = dimension_name
           )
        THEN
            RAISE EXCEPTION 'Join keys must be distinct and explicitly modeled';
        END IF;

        SELECT col->>'data_type' INTO fact_type
          FROM jsonb_array_elements(fc.source_schema->'columns') col
          WHERE col->>'name' = fact_name;
        SELECT col->>'data_type' INTO dimension_type
          FROM jsonb_array_elements(dc.source_schema->'columns') col
          WHERE col->>'name' = dimension_name;
        IF fact_type IS NULL OR dimension_type IS NULL
           OR fact_type <> dimension_type OR fact_type = 'FLOAT' THEN
            RAISE EXCEPTION 'Incompatible relationship key types';
        END IF;

        fact_names := array_append(fact_names, fact_name);
        dimension_names := array_append(dimension_names, dimension_name);
        pair_count := pair_count + 1;
    END LOOP;

    IF pair_count <> jsonb_array_length(dim_business_keys) THEN
        RAISE EXCEPTION 'Incomplete dimension business key mapping';
    END IF;

    IF dc.configuration->>'scd_type' = 'SCD_TYPE_2' THEN
        IF NEW.temporal_policy <> 'EVENT_TIME_AS_OF'
           OR NEW.fact_event_time_column IS DISTINCT FROM fc.configuration->'event_time'->>'column'
           OR fc.configuration->'event_time' IS NULL
           OR fc.configuration->'event_time' = 'null'::jsonb
           OR dc.configuration->'effective_time' IS NULL
           OR dc.configuration->'effective_time' = 'null'::jsonb
           OR fc.configuration->'event_time'->>'interpretation'
                 IS DISTINCT FROM dc.configuration->'effective_time'->>'interpretation'
           OR fc.configuration->'event_time'->>'timezone'
                 IS DISTINCT FROM dc.configuration->'effective_time'->>'timezone'
        THEN
            RAISE EXCEPTION 'SCD2 relationship requires compatible historical times';
        END IF;

        SELECT col->>'data_type' INTO fact_type
          FROM jsonb_array_elements(fc.source_schema->'columns') col
          WHERE col->>'name' = NEW.fact_event_time_column;
        SELECT col->>'data_type' INTO dimension_type
          FROM jsonb_array_elements(dc.source_schema->'columns') col
          WHERE col->>'name' = dc.configuration->'effective_time'->>'column';
        IF fact_type NOT IN ('DATE','TIMESTAMP')
           OR fact_type IS DISTINCT FROM dimension_type THEN
            RAISE EXCEPTION 'Historical time columns must have the same type';
        END IF;
    ELSIF NEW.temporal_policy <> 'CURRENT_MEMBER' THEN
        RAISE EXCEPTION 'SCD1 relationship requires current-member semantics';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER warehouse_relationship_insert
    BEFORE INSERT ON warehouse_model_relationships
    FOR EACH ROW EXECUTE FUNCTION warehouse_relationship_insert_guard();
COMMIT;
