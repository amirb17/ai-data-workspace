-- Phase 11B: immutable, explicitly configured semantic metadata only.
-- Existing 11A versions remain unconfigured; no backfill, execution or business-row changes.
BEGIN;
CREATE TABLE warehouse_model_configurations (
    warehouse_model_version_id BIGINT PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    warehouse_model_id BIGINT NOT NULL,
    source_dataset_id BIGINT NOT NULL,
    source_dataset_version_id BIGINT NOT NULL,
    source_policy_id BIGINT NOT NULL,
    source_rule_version INTEGER NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1),
    validation_version INTEGER NOT NULL DEFAULT 1 CHECK(validation_version=1),
    model_type TEXT NOT NULL CHECK(model_type IN ('FACT','DIMENSION')),
    configuration JSONB NOT NULL CHECK(jsonb_typeof(configuration)='object'
        AND configuration->'schema_version'='1'::jsonb AND configuration->>'model_type'=model_type),
    source_schema JSONB NOT NULL CHECK(jsonb_typeof(source_schema)='object' AND source_schema->'schema_version'='1'::jsonb
        AND jsonb_typeof(source_schema->'columns')='array' AND jsonb_array_length(source_schema->'columns') BETWEEN 1 AND 400),
    source_rule_sha256 TEXT NOT NULL CHECK(source_rule_sha256 ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT NOT NULL REFERENCES users(user_id),
    FOREIGN KEY(workspace_id,warehouse_model_id,warehouse_model_version_id)
        REFERENCES warehouse_model_versions(workspace_id,warehouse_model_id,warehouse_model_version_id),
    FOREIGN KEY(warehouse_model_version_id,source_dataset_id)
        REFERENCES warehouse_model_sources(warehouse_model_version_id,source_dataset_id),
    FOREIGN KEY(source_policy_id,source_dataset_id,source_dataset_version_id)
        REFERENCES dataset_load_policies(policy_id,dataset_id,dataset_version_id),
    FOREIGN KEY(source_dataset_version_id,source_rule_version)
        REFERENCES approved_rule_policies(dataset_version_id,rule_version)
);
CREATE INDEX warehouse_configuration_scope ON warehouse_model_configurations(workspace_id,warehouse_model_id);
CREATE INDEX warehouse_configuration_policy ON warehouse_model_configurations(source_policy_id);

CREATE FUNCTION warehouse_modeling_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v warehouse_model_versions; c JSONB; fields TEXT[]; identity_fields JSONB; attributes JSONB;
    time_policy JSONB; item JSONB; role TEXT; seen TEXT[] := ARRAY[]::TEXT[]; source_names TEXT[];
BEGIN
    IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Semantic configuration is immutable; create a new version'; END IF;
    SELECT * INTO v FROM warehouse_model_versions WHERE warehouse_model_version_id=NEW.warehouse_model_version_id FOR UPDATE;
    IF v.status IS DISTINCT FROM 'DRAFT' OR NOT EXISTS(SELECT 1 FROM warehouse_model_sources s JOIN datasets d ON d.dataset_id=s.source_dataset_id
        JOIN workspaces w ON w.workspace_id=s.workspace_id WHERE s.warehouse_model_version_id=NEW.warehouse_model_version_id
        AND s.source_dataset_id=NEW.source_dataset_id AND s.purpose='PRIMARY' AND d.workspace_id=NEW.workspace_id
        AND d.owner=w.owner AND d.status='ACTIVE') THEN RAISE EXCEPTION 'Configuration requires a draft and scoped active primary source'; END IF;
    c:=NEW.configuration;
    IF c->'schema_version' IS DISTINCT FROM '1'::jsonb OR c->>'model_type' IS DISTINCT FROM NEW.model_type
        OR NEW.source_schema->'schema_version' IS DISTINCT FROM '1'::jsonb THEN RAISE EXCEPTION 'Unsupported semantic schema version/type'; END IF;
    IF NOT c ?& ARRAY['schema_version','model_type'] OR NOT NEW.source_schema ?& ARRAY['schema_version','columns']
        OR NEW.source_schema-ARRAY['schema_version','columns']<>'{}'::jsonb THEN RAISE EXCEPTION 'Invalid versioned schema shape'; END IF;
    IF NEW.model_type='FACT' THEN
        fields:=ARRAY['schema_version','model_type','grain','event_time','identity_null_policy'];
        IF NOT c ?& fields OR c->>'identity_null_policy' IS DISTINCT FROM 'REJECT'
            OR jsonb_typeof(c->'grain') IS DISTINCT FROM 'object'
            OR NOT (c->'grain') ?& ARRAY['description','identity_columns']
            OR (c->'grain')-ARRAY['description','identity_columns']<>'{}'::jsonb
            OR jsonb_typeof(c->'grain'->'description') IS DISTINCT FROM 'string'
            OR length(btrim(c->'grain'->>'description')) NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'Explicit fact grain required'; END IF;
        identity_fields:=c->'grain'->'identity_columns'; attributes:='[]'; time_policy:=c->'event_time';
    ELSE
        fields:=ARRAY['schema_version','model_type','business_keys','scd_type','tracked_attributes','nonhistorical_attributes',
            'effective_time','history_coverage','missing_member_policy','null_business_key_policy','snapshot_absence_policy'];
        IF NOT c ?& fields OR COALESCE(c->>'scd_type','') NOT IN ('SCD_TYPE_1','SCD_TYPE_2')
            OR c->>'history_coverage' IS DISTINCT FROM 'FROM_FIRST_OBSERVED_EVENT'
            OR COALESCE(c->>'missing_member_policy','') NOT IN ('REJECT','INFER_MEMBER')
            OR c->>'null_business_key_policy' IS DISTINCT FROM 'REJECT'
            OR COALESCE(c->>'snapshot_absence_policy','') NOT IN ('KEEP','DEACTIVATE_ON_COMPLETE')
            OR jsonb_typeof(c->'tracked_attributes') IS DISTINCT FROM 'array'
            OR jsonb_typeof(c->'nonhistorical_attributes') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Invalid dimension policy'; END IF;
        identity_fields:=c->'business_keys'; attributes:=(c->'tracked_attributes')||(c->'nonhistorical_attributes'); time_policy:=c->'effective_time';
        IF jsonb_array_length(attributes)=0 OR (c->>'scd_type'='SCD_TYPE_2' AND
            (jsonb_array_length(c->'tracked_attributes')=0 OR time_policy='null'::jsonb)) THEN RAISE EXCEPTION 'SCD2 requires tracked attributes/effective time'; END IF;
    END IF;
    IF c-fields<>'{}'::jsonb OR jsonb_typeof(identity_fields) IS DISTINCT FROM 'array'
        OR jsonb_array_length(identity_fields) NOT BETWEEN 1 AND 32 THEN RAISE EXCEPTION 'Invalid configuration fields/identity'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.source_schema->'columns') LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR NOT item ?& ARRAY['name','data_type','nullable']
            OR item-ARRAY['name','data_type','nullable']<>'{}'::jsonb OR jsonb_typeof(item->'name') IS DISTINCT FROM 'string'
            OR COALESCE(item->>'data_type','') NOT IN ('STRING','INTEGER','DECIMAL','FLOAT','BOOLEAN','DATE','TIMESTAMP')
            OR COALESCE(jsonb_typeof(item->'nullable'),'') NOT IN ('boolean','null') THEN RAISE EXCEPTION 'Invalid source column shape'; END IF;
        IF item->>'name'=ANY(COALESCE(source_names,ARRAY[]::TEXT[])) THEN RAISE EXCEPTION 'Duplicate source column'; END IF;
        source_names:=array_append(source_names,item->>'name');
    END LOOP;
    IF cardinality(source_names)<>(SELECT jsonb_array_length(schema_columns) FROM dataset_load_policies WHERE policy_id=NEW.source_policy_id)
        OR EXISTS(SELECT 1 FROM dataset_load_policies p,jsonb_array_elements(p.schema_columns) e
            WHERE p.policy_id=NEW.source_policy_id AND NOT (e->>'name')=ANY(source_names)) THEN
        RAISE EXCEPTION 'Source schema names must match pinned contract';
    END IF;
    IF NEW.model_type='DIMENSION' AND c->>'snapshot_absence_policy'='DEACTIVATE_ON_COMPLETE' AND NOT EXISTS(
        SELECT 1 FROM dataset_load_policies WHERE policy_id=NEW.source_policy_id AND load_strategy='SNAPSHOT' AND snapshot_coverage='COMPLETE')
        THEN RAISE EXCEPTION 'Absence deactivation requires COMPLETE snapshot authority'; END IF;
    IF time_policy<>'null'::jsonb THEN
        IF jsonb_typeof(time_policy) IS DISTINCT FROM 'object' OR NOT time_policy ?& ARRAY['column','interpretation','timezone']
            OR time_policy-ARRAY['column','interpretation','timezone']<>'{}'::jsonb
            OR COALESCE(time_policy->>'interpretation','') NOT IN ('UTC','SOURCE_TIMEZONE')
            OR (time_policy->>'interpretation'='UTC' AND time_policy->'timezone'<>'null'::jsonb)
            OR (time_policy->>'interpretation'='SOURCE_TIMEZONE' AND NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=time_policy->>'timezone'))
            THEN RAISE EXCEPTION 'Explicit valid time policy required'; END IF;
        attributes:=attributes||jsonb_build_array(time_policy->'column');
        IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.source_schema->'columns') e
            WHERE e->>'name'=time_policy->>'column' AND e->>'data_type' IN ('DATE','TIMESTAMP')) THEN RAISE EXCEPTION 'Time column must be typed'; END IF;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(identity_fields||attributes) LOOP
        role:=item#>>'{}';
        IF jsonb_typeof(item) IS DISTINCT FROM 'string' OR role IS NULL OR length(role) NOT BETWEEN 1 AND 255
            OR role<>btrim(role) OR role ~ '^_(dq|source|bronze|gold|datarise|application)_'
            OR role=ANY(seen) OR NOT role=ANY(source_names) THEN RAISE EXCEPTION 'Missing/duplicate/overlapping business column'; END IF;
        seen:=array_append(seen,role);
    END LOOP;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.source_schema->'columns') e
        WHERE (e->'name') IN (SELECT value FROM jsonb_array_elements(identity_fields)) AND e->>'data_type'='FLOAT')
        THEN RAISE EXCEPTION 'Float identities unsupported'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER warehouse_modeling_immutable BEFORE INSERT OR UPDATE OR DELETE ON warehouse_model_configurations
    FOR EACH ROW EXECUTE FUNCTION warehouse_modeling_guard();
CREATE TRIGGER warehouse_modeling_no_truncate BEFORE TRUNCATE ON warehouse_model_configurations EXECUTE FUNCTION warehouse_no_truncate();

CREATE FUNCTION warehouse_modeling_approval_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status='DRAFT' AND NEW.status='APPROVED' AND NOT EXISTS(SELECT 1 FROM warehouse_model_configurations
        WHERE warehouse_model_version_id=NEW.warehouse_model_version_id) THEN
        RAISE EXCEPTION 'New approvals require explicitly validated FACT/DIMENSION configuration';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER warehouse_modeling_approval BEFORE UPDATE ON warehouse_model_versions
    FOR EACH ROW EXECUTE FUNCTION warehouse_modeling_approval_guard();
COMMIT;
