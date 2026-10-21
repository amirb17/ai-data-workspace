-- Phase 11A: metadata only. 0028 is reserved in migrations/pending.
-- No existing business tables, rows, functions or pointers are modified.
BEGIN;

CREATE TABLE warehouse_models (
    warehouse_model_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
    display_name TEXT NOT NULL CHECK(length(btrim(display_name)) BETWEEN 1 AND 120),
    normalized_name TEXT NOT NULL CHECK(length(normalized_name) BETWEEN 1 AND 240 AND normalized_name=btrim(normalized_name)),
    key_namespace UUID NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','APPROVED','ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT NOT NULL REFERENCES users(user_id),
    archived_at TIMESTAMPTZ,
    UNIQUE(workspace_id,warehouse_model_id),
    UNIQUE(workspace_id,normalized_name),
    CHECK((status='ARCHIVED')=(archived_at IS NOT NULL))
);

CREATE TABLE warehouse_model_versions (
    warehouse_model_version_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    warehouse_model_id BIGINT NOT NULL,
    version_number INTEGER NOT NULL CHECK(version_number>0),
    configuration_version INTEGER NOT NULL DEFAULT 1 CHECK(configuration_version=1),
    key_algorithm_version INTEGER NOT NULL DEFAULT 1 CHECK(key_algorithm_version=1),
    key_strategy TEXT NOT NULL DEFAULT 'DETERMINISTIC' CHECK(key_strategy='DETERMINISTIC'),
    key_storage_type TEXT NOT NULL DEFAULT 'STRING' CHECK(key_storage_type='STRING'),
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','APPROVED','ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT NOT NULL REFERENCES users(user_id),
    reviewed_at TIMESTAMPTZ,
    reviewed_by BIGINT REFERENCES users(user_id),
    FOREIGN KEY(workspace_id,warehouse_model_id) REFERENCES warehouse_models(workspace_id,warehouse_model_id),
    UNIQUE(warehouse_model_id,version_number),
    UNIQUE(workspace_id,warehouse_model_id,warehouse_model_version_id),
    CHECK((reviewed_by IS NULL)=(reviewed_at IS NULL)),
    CHECK(status<>'APPROVED' OR reviewed_by IS NOT NULL)
);
CREATE UNIQUE INDEX warehouse_one_approved_version ON warehouse_model_versions(warehouse_model_id) WHERE status='APPROVED';

CREATE TABLE warehouse_model_sources (
    source_mapping_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    warehouse_model_id BIGINT NOT NULL,
    warehouse_model_version_id BIGINT NOT NULL,
    source_dataset_id BIGINT NOT NULL REFERENCES datasets(dataset_id),
    purpose TEXT NOT NULL CHECK(purpose IN ('PRIMARY','DEPENDENCY')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY(workspace_id,warehouse_model_id,warehouse_model_version_id)
        REFERENCES warehouse_model_versions(workspace_id,warehouse_model_id,warehouse_model_version_id),
    UNIQUE(warehouse_model_version_id,source_dataset_id)
);
CREATE UNIQUE INDEX warehouse_one_primary_source ON warehouse_model_sources(warehouse_model_version_id) WHERE purpose='PRIMARY';
CREATE INDEX warehouse_sources_dataset ON warehouse_model_sources(source_dataset_id);

CREATE TABLE warehouse_resources (
    resource_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    warehouse_model_id BIGINT NOT NULL,
    warehouse_model_version_id BIGINT NOT NULL,
    provider TEXT NOT NULL CHECK(provider='BIGQUERY'),
    environment TEXT NOT NULL CHECK(environment IN ('development','production')),
    location TEXT NOT NULL CHECK(location ~ '^[a-z][a-z0-9-]{1,62}$'),
    generation INTEGER NOT NULL CHECK(generation>0),
    physical_reference JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'REGISTERED' CHECK(status IN ('REGISTERED','ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT NOT NULL REFERENCES users(user_id),
    FOREIGN KEY(workspace_id,warehouse_model_id,warehouse_model_version_id)
        REFERENCES warehouse_model_versions(workspace_id,warehouse_model_id,warehouse_model_version_id),
    UNIQUE(workspace_id,warehouse_model_id,warehouse_model_version_id,resource_id,environment),
    UNIQUE(warehouse_model_id,provider,environment,generation),
    -- A physical table cannot be registered under another workspace/environment/model.
    UNIQUE(provider,physical_reference),
    CHECK(environment<>'production' OR location='asia-south1'),
    CHECK(jsonb_typeof(physical_reference)='object'
        AND physical_reference ?& ARRAY['reference_version','project','dataset','table']
        AND physical_reference-ARRAY['reference_version','project','dataset','table']='{}'::jsonb
        AND physical_reference->'reference_version'='1'::jsonb
        AND jsonb_typeof(physical_reference->'project')='string'
        AND jsonb_typeof(physical_reference->'dataset')='string'
        AND jsonb_typeof(physical_reference->'table')='string'
        AND physical_reference->>'project' ~ '^[a-z][a-z0-9-]{4,28}[a-z0-9]$'
        AND length(physical_reference->>'dataset')<=1024
        AND length(physical_reference->>'table')<=1024
        AND physical_reference->>'dataset' ~ '^[A-Za-z_][A-Za-z0-9_]*$'
        AND physical_reference->>'table' ~ '^[A-Za-z_][A-Za-z0-9_]*$')
);

CREATE TABLE warehouse_operations (
    operation_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    warehouse_model_id BIGINT NOT NULL,
    warehouse_model_version_id BIGINT NOT NULL,
    resource_id BIGINT NOT NULL,
    environment TEXT NOT NULL CHECK(environment IN ('development','production')),
    operation_type TEXT NOT NULL CHECK(operation_type IN ('INITIAL_LOAD','INCREMENTAL','REPLAY','BACKFILL','REBUILD')),
    reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT NOT NULL REFERENCES users(user_id),
    FOREIGN KEY(workspace_id,warehouse_model_id,warehouse_model_version_id,resource_id,environment)
        REFERENCES warehouse_resources(workspace_id,warehouse_model_id,warehouse_model_version_id,resource_id,environment),
    UNIQUE(workspace_id,operation_id)
);
CREATE INDEX warehouse_operations_model ON warehouse_operations(workspace_id,warehouse_model_id,operation_id DESC);

CREATE TABLE warehouse_runs (
    warehouse_run_id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL,
    operation_id BIGINT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK(attempt_number>0),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT NOT NULL REFERENCES users(user_id),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failure_code TEXT CHECK(failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    FOREIGN KEY(workspace_id,operation_id) REFERENCES warehouse_operations(workspace_id,operation_id),
    UNIQUE(operation_id,attempt_number),
    CHECK((status='PENDING' AND started_at IS NULL AND completed_at IS NULL AND failure_code IS NULL)
        OR (status='RUNNING' AND started_at IS NOT NULL AND completed_at IS NULL AND failure_code IS NULL)
        OR (status='SUCCEEDED' AND started_at IS NOT NULL AND completed_at IS NOT NULL AND failure_code IS NULL)
        OR (status='FAILED' AND started_at IS NOT NULL AND completed_at IS NOT NULL AND failure_code IS NOT NULL)),
    CHECK(completed_at IS NULL OR completed_at>=started_at)
);
CREATE UNIQUE INDEX warehouse_one_open_attempt ON warehouse_runs(operation_id) WHERE status IN ('PENDING','RUNNING');
CREATE INDEX warehouse_run_history ON warehouse_runs(workspace_id,operation_id,attempt_number DESC);

CREATE FUNCTION warehouse_guard_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Warehouse history cannot be deleted'; END IF;
    IF TG_TABLE_NAME='warehouse_models' THEN
        IF (to_jsonb(NEW)-ARRAY['display_name','normalized_name','status','archived_at']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['display_name','normalized_name','status','archived_at']) OR OLD.status='ARCHIVED' THEN
            RAISE EXCEPTION 'Warehouse model identity/actor is immutable';
        END IF;
        IF NEW.status<>OLD.status AND NOT ((OLD.status='DRAFT' AND NEW.status IN ('APPROVED','ARCHIVED'))
            OR (OLD.status='APPROVED' AND NEW.status='ARCHIVED')) THEN RAISE EXCEPTION 'Invalid model transition'; END IF;
        IF NEW.status='APPROVED' AND NOT EXISTS(SELECT 1 FROM warehouse_model_versions
            WHERE warehouse_model_id=NEW.warehouse_model_id AND status='APPROVED') THEN
            RAISE EXCEPTION 'Approved model requires an approved version';
        END IF;
    ELSIF TG_TABLE_NAME='warehouse_model_versions' THEN
        IF (to_jsonb(NEW)-ARRAY['status','reviewed_by','reviewed_at']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['status','reviewed_by','reviewed_at']) OR OLD.status='ARCHIVED' THEN
            RAISE EXCEPTION 'Version configuration/identity/actor is immutable; create a new version';
        END IF;
        IF NOT ((OLD.status='DRAFT' AND NEW.status IN ('APPROVED','ARCHIVED')) OR
            (OLD.status='APPROVED' AND NEW.status='ARCHIVED')) THEN RAISE EXCEPTION 'Invalid version transition'; END IF;
        IF OLD.reviewed_by IS NOT NULL AND ROW(NEW.reviewed_by,NEW.reviewed_at) IS DISTINCT FROM ROW(OLD.reviewed_by,OLD.reviewed_at)
            THEN RAISE EXCEPTION 'Review actor is immutable'; END IF;
        IF NEW.status='ARCHIVED' AND ROW(NEW.reviewed_by,NEW.reviewed_at) IS DISTINCT FROM ROW(OLD.reviewed_by,OLD.reviewed_at)
            THEN RAISE EXCEPTION 'Archival cannot change review provenance'; END IF;
        IF NEW.status='APPROVED' THEN
            PERFORM 1 FROM warehouse_models WHERE warehouse_model_id=NEW.warehouse_model_id AND status<>'ARCHIVED' FOR UPDATE;
            IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM warehouse_model_sources
                WHERE warehouse_model_version_id=NEW.warehouse_model_version_id AND purpose='PRIMARY') THEN
                RAISE EXCEPTION 'Approval requires an available model and primary source';
            END IF;
            IF EXISTS(SELECT 1 FROM warehouse_model_sources s JOIN datasets d ON d.dataset_id=s.source_dataset_id
                JOIN workspaces w ON w.workspace_id=s.workspace_id WHERE s.warehouse_model_version_id=NEW.warehouse_model_version_id
                AND (d.workspace_id IS DISTINCT FROM s.workspace_id OR d.owner IS DISTINCT FROM w.owner OR d.status<>'ACTIVE')) THEN
                RAISE EXCEPTION 'Source membership changed before approval';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='warehouse_resources' THEN
        IF (to_jsonb(NEW)-'status') IS DISTINCT FROM (to_jsonb(OLD)-'status') OR
            OLD.status<>'REGISTERED' OR NEW.status<>'ARCHIVED' THEN RAISE EXCEPTION 'Resource binding is immutable'; END IF;
    ELSIF TG_TABLE_NAME='warehouse_operations' THEN
        RAISE EXCEPTION 'Operation pins/actor are immutable';
    ELSIF TG_TABLE_NAME='warehouse_runs' THEN
        IF (to_jsonb(NEW)-ARRAY['status','started_at','completed_at','failure_code']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['status','started_at','completed_at','failure_code']) OR
            OLD.status IN ('SUCCEEDED','FAILED') THEN RAISE EXCEPTION 'Run pins/terminal history are immutable'; END IF;
        IF NOT ((OLD.status='PENDING' AND NEW.status='RUNNING') OR
            (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED'))) THEN RAISE EXCEPTION 'Invalid run transition'; END IF;
        IF OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
            RAISE EXCEPTION 'Run start time is immutable'; END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION warehouse_guard_sources() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v warehouse_model_versions; d datasets; scope workspaces;
BEGIN
    IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'Replace draft source mappings explicitly'; END IF;
    IF TG_OP='DELETE' THEN
        SELECT * INTO v FROM warehouse_model_versions WHERE warehouse_model_version_id=OLD.warehouse_model_version_id FOR UPDATE;
        IF v.status<>'DRAFT' THEN RAISE EXCEPTION 'Approved/archived sources are immutable'; END IF;
        RETURN OLD;
    END IF;
    SELECT * INTO v FROM warehouse_model_versions WHERE warehouse_model_version_id=NEW.warehouse_model_version_id FOR UPDATE;
    SELECT * INTO scope FROM workspaces WHERE workspace_id=NEW.workspace_id FOR SHARE;
    SELECT * INTO d FROM datasets WHERE dataset_id=NEW.source_dataset_id FOR SHARE;
    IF v.status IS DISTINCT FROM 'DRAFT' OR d.workspace_id IS DISTINCT FROM NEW.workspace_id OR
        d.owner IS DISTINCT FROM scope.owner OR d.status IS DISTINCT FROM 'ACTIVE' THEN
        RAISE EXCEPTION 'Source requires a same-workspace active dataset and draft version';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION warehouse_guard_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v warehouse_model_versions; m warehouse_models; r warehouse_resources; last_run warehouse_runs;
BEGIN
    IF TG_TABLE_NAME='warehouse_models' THEN
        IF NEW.status<>'DRAFT' THEN RAISE EXCEPTION 'New models start as drafts'; END IF;
    ELSIF TG_TABLE_NAME='warehouse_model_versions' THEN
        SELECT * INTO m FROM warehouse_models WHERE warehouse_model_id=NEW.warehouse_model_id FOR UPDATE;
        IF m.status='ARCHIVED' OR NEW.status<>'DRAFT' OR NEW.reviewed_by IS NOT NULL THEN RAISE EXCEPTION 'New versions start as unreviewed drafts'; END IF;
    ELSIF TG_TABLE_NAME IN ('warehouse_resources','warehouse_operations') THEN
        SELECT * INTO m FROM warehouse_models WHERE warehouse_model_id=NEW.warehouse_model_id FOR SHARE;
        SELECT * INTO v FROM warehouse_model_versions WHERE warehouse_model_version_id=NEW.warehouse_model_version_id FOR SHARE;
        IF m.status<>'APPROVED' OR v.status<>'APPROVED' THEN RAISE EXCEPTION 'Approved model/version required'; END IF;
        IF TG_TABLE_NAME='warehouse_resources' THEN
            IF NEW.status<>'REGISTERED' THEN RAISE EXCEPTION 'Resources start registered'; END IF;
        ELSE
            SELECT * INTO r FROM warehouse_resources WHERE resource_id=NEW.resource_id FOR SHARE;
            IF r.status<>'REGISTERED' THEN RAISE EXCEPTION 'Registered resource required'; END IF;
        END IF;
    ELSIF TG_TABLE_NAME='warehouse_runs' THEN
        PERFORM 1 FROM warehouse_operations WHERE operation_id=NEW.operation_id FOR UPDATE;
        SELECT * INTO last_run FROM warehouse_runs WHERE operation_id=NEW.operation_id ORDER BY attempt_number DESC LIMIT 1;
        IF NEW.status<>'PENDING' OR NEW.attempt_number<>COALESCE(last_run.attempt_number,0)+1 OR
            (last_run.warehouse_run_id IS NOT NULL AND last_run.status<>'FAILED') THEN
            RAISE EXCEPTION 'New pending attempt requires no prior attempt or a failed predecessor';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER warehouse_model_history BEFORE UPDATE OR DELETE ON warehouse_models FOR EACH ROW EXECUTE FUNCTION warehouse_guard_history();
CREATE TRIGGER warehouse_version_history BEFORE UPDATE OR DELETE ON warehouse_model_versions FOR EACH ROW EXECUTE FUNCTION warehouse_guard_history();
CREATE TRIGGER warehouse_source_history BEFORE INSERT OR UPDATE OR DELETE ON warehouse_model_sources FOR EACH ROW EXECUTE FUNCTION warehouse_guard_sources();
CREATE TRIGGER warehouse_resource_history BEFORE UPDATE OR DELETE ON warehouse_resources FOR EACH ROW EXECUTE FUNCTION warehouse_guard_history();
CREATE TRIGGER warehouse_operation_history BEFORE UPDATE OR DELETE ON warehouse_operations FOR EACH ROW EXECUTE FUNCTION warehouse_guard_history();
CREATE TRIGGER warehouse_run_history BEFORE UPDATE OR DELETE ON warehouse_runs FOR EACH ROW EXECUTE FUNCTION warehouse_guard_history();
CREATE TRIGGER warehouse_model_insert BEFORE INSERT ON warehouse_models FOR EACH ROW EXECUTE FUNCTION warehouse_guard_insert();
CREATE TRIGGER warehouse_version_insert BEFORE INSERT ON warehouse_model_versions FOR EACH ROW EXECUTE FUNCTION warehouse_guard_insert();
CREATE TRIGGER warehouse_resource_insert BEFORE INSERT ON warehouse_resources FOR EACH ROW EXECUTE FUNCTION warehouse_guard_insert();
CREATE TRIGGER warehouse_operation_insert BEFORE INSERT ON warehouse_operations FOR EACH ROW EXECUTE FUNCTION warehouse_guard_insert();
CREATE TRIGGER warehouse_run_insert BEFORE INSERT ON warehouse_runs FOR EACH ROW EXECUTE FUNCTION warehouse_guard_insert();

CREATE FUNCTION warehouse_no_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Warehouse history cannot be truncated'; END $$;
CREATE TRIGGER warehouse_models_no_truncate BEFORE TRUNCATE ON warehouse_models EXECUTE FUNCTION warehouse_no_truncate();
CREATE TRIGGER warehouse_versions_no_truncate BEFORE TRUNCATE ON warehouse_model_versions EXECUTE FUNCTION warehouse_no_truncate();
CREATE TRIGGER warehouse_sources_no_truncate BEFORE TRUNCATE ON warehouse_model_sources EXECUTE FUNCTION warehouse_no_truncate();
CREATE TRIGGER warehouse_resources_no_truncate BEFORE TRUNCATE ON warehouse_resources EXECUTE FUNCTION warehouse_no_truncate();
CREATE TRIGGER warehouse_operations_no_truncate BEFORE TRUNCATE ON warehouse_operations EXECUTE FUNCTION warehouse_no_truncate();
CREATE TRIGGER warehouse_runs_no_truncate BEFORE TRUNCATE ON warehouse_runs EXECUTE FUNCTION warehouse_no_truncate();
COMMIT;
