-- Additive reliability metadata. Never bootstrap or execute source/warehouse work.
BEGIN;
CREATE TABLE warehouse_accepted_manifests (
    manifest_id TEXT PRIMARY KEY CHECK(manifest_id ~ '^[a-f0-9]{64}$'),
    workspace_id BIGINT NOT NULL,
    warehouse_model_id BIGINT NOT NULL,
    warehouse_model_version_id BIGINT NOT NULL,
    source_mapping_id BIGINT NOT NULL REFERENCES warehouse_model_sources(source_mapping_id),
    source_dataset_id BIGINT NOT NULL,
    source_state_id BIGINT NOT NULL,
    position BIGINT NOT NULL CHECK(position>0),
    previous_manifest_id TEXT REFERENCES warehouse_accepted_manifests(manifest_id),
    checksum TEXT NOT NULL CHECK(checksum ~ '^[a-f0-9]{64}$'),
    body JSONB NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=70000000
        AND body ?& ARRAY['manifest_id','checksum','position','pin','events','event_count','material_count','contract_version',
            'source_mapping_id','model_version_id','configuration_sha256']
        AND body->'contract_version'='1'::jsonb AND jsonb_typeof(body->'events')='array'
        AND jsonb_array_length(body->'events')<=1000 AND (body->>'event_count')::bigint=jsonb_array_length(body->'events')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY(workspace_id,warehouse_model_id,warehouse_model_version_id)
        REFERENCES warehouse_model_versions(workspace_id,warehouse_model_id,warehouse_model_version_id),
    FOREIGN KEY(source_dataset_id,source_state_id) REFERENCES dataset_state_versions(dataset_id,state_id),
    UNIQUE(source_mapping_id,position), UNIQUE(source_mapping_id,source_state_id),
    UNIQUE(workspace_id,manifest_id)
);
CREATE TABLE warehouse_accepted_events (
    manifest_id TEXT NOT NULL REFERENCES warehouse_accepted_manifests(manifest_id),
    event_id TEXT NOT NULL CHECK(event_id ~ '^[a-f0-9]{64}$'),
    checksum TEXT NOT NULL CHECK(checksum ~ '^[a-f0-9]{64}$'),
    outcome TEXT NOT NULL CHECK(outcome IN ('INSERTED','UPDATED','UNCHANGED','REACTIVATED','DEACTIVATED')),
    payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='array' AND jsonb_array_length(payload)<=128 AND octet_length(payload::text)<=70000),
    PRIMARY KEY(manifest_id,event_id)
);
CREATE TABLE warehouse_outbox (
    manifest_id TEXT PRIMARY KEY REFERENCES warehouse_accepted_manifests(manifest_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE warehouse_execution_intents (
    operation_id BIGINT PRIMARY KEY REFERENCES warehouse_operations(operation_id),
    workspace_id BIGINT NOT NULL,
    warehouse_model_id BIGINT NOT NULL,
    warehouse_model_version_id BIGINT NOT NULL,
    resource_id BIGINT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation>0),
    request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 1 AND 120),
    application_token TEXT NOT NULL UNIQUE CHECK(application_token ~ '^[a-f0-9]{64}$'),
    dispatch_key TEXT NOT NULL UNIQUE CHECK(dispatch_key ~ '^[a-f0-9]{64}$'),
    intent_sha256 TEXT NOT NULL CHECK(intent_sha256 ~ '^[a-f0-9]{64}$'),
    body JSONB NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=1048576),
    FOREIGN KEY(workspace_id,operation_id) REFERENCES warehouse_operations(workspace_id,operation_id),
    FOREIGN KEY(workspace_id,warehouse_model_id,warehouse_model_version_id)
        REFERENCES warehouse_model_versions(workspace_id,warehouse_model_id,warehouse_model_version_id),
    UNIQUE(workspace_id,warehouse_model_id,warehouse_model_version_id,resource_id,request_key)
);
-- One writer per model/resource generation, including replay/backfill intents.
CREATE TABLE warehouse_writer_leases (
    resource_id BIGINT PRIMARY KEY REFERENCES warehouse_resources(resource_id),
    operation_id BIGINT NOT NULL REFERENCES warehouse_execution_intents(operation_id),
    warehouse_run_id BIGINT NOT NULL REFERENCES warehouse_runs(warehouse_run_id),
    fence BIGINT NOT NULL CHECK(fence>0),
    worker TEXT NOT NULL CHECK(length(worker) BETWEEN 1 AND 120),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE warehouse_run_journal (
    journal_id BIGSERIAL PRIMARY KEY,
    warehouse_run_id BIGINT NOT NULL REFERENCES warehouse_runs(warehouse_run_id),
    fence BIGINT NOT NULL CHECK(fence>0),
    stage TEXT NOT NULL CHECK(stage IN ('CLAIMED','PLANNED','TARGET_RECEIPT','RECONCILED','PUBLISHED','BLOCKED')),
    evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence)='object' AND octet_length(evidence::text)<=1048576),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX warehouse_journal_run ON warehouse_run_journal(warehouse_run_id,journal_id);
CREATE TABLE warehouse_reconciliations (
    reconciliation_id BIGSERIAL PRIMARY KEY,
    operation_id BIGINT NOT NULL REFERENCES warehouse_execution_intents(operation_id),
    warehouse_run_id BIGINT NOT NULL REFERENCES warehouse_runs(warehouse_run_id),
    fence BIGINT NOT NULL CHECK(fence>0),
    passed BOOLEAN NOT NULL,
    evidence JSONB NOT NULL CHECK(jsonb_typeof(evidence)='object' AND octet_length(evidence::text)<=1048576),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(reconciliation_id,operation_id,warehouse_run_id,passed)
);
CREATE TABLE warehouse_publications (
    publication_id BIGSERIAL PRIMARY KEY,
    operation_id BIGINT NOT NULL UNIQUE REFERENCES warehouse_execution_intents(operation_id),
    warehouse_run_id BIGINT NOT NULL REFERENCES warehouse_runs(warehouse_run_id),
    reconciliation_id BIGINT NOT NULL,
    reconciled BOOLEAN NOT NULL DEFAULT true CHECK(reconciled),
    resource_id BIGINT NOT NULL REFERENCES warehouse_resources(resource_id),
    revision BIGINT NOT NULL CHECK(revision>0),
    previous_publication_id BIGINT REFERENCES warehouse_publications(publication_id),
    source_vector JSONB NOT NULL CHECK(jsonb_typeof(source_vector)='array' AND jsonb_array_length(source_vector) BETWEEN 1 AND 32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY(reconciliation_id,operation_id,warehouse_run_id,reconciled)
        REFERENCES warehouse_reconciliations(reconciliation_id,operation_id,warehouse_run_id,passed),
    UNIQUE(resource_id,revision), UNIQUE(resource_id,publication_id)
);
CREATE TABLE warehouse_publication_heads (
    resource_id BIGINT PRIMARY KEY REFERENCES warehouse_resources(resource_id),
    publication_id BIGINT NOT NULL,
    revision BIGINT NOT NULL CHECK(revision>0),
    FOREIGN KEY(resource_id,publication_id) REFERENCES warehouse_publications(resource_id,publication_id)
);
CREATE TABLE warehouse_checkpoints (
    resource_id BIGINT NOT NULL REFERENCES warehouse_resources(resource_id),
    source_mapping_id BIGINT NOT NULL REFERENCES warehouse_model_sources(source_mapping_id),
    position BIGINT NOT NULL CHECK(position>=0),
    version BIGINT NOT NULL CHECK(version>0),
    publication_id BIGINT NOT NULL REFERENCES warehouse_publications(publication_id),
    PRIMARY KEY(resource_id,source_mapping_id)
);

CREATE FUNCTION warehouse_reliability_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Warehouse reliability evidence is append-only'; END $$;
DO $$ DECLARE t TEXT; BEGIN
    FOREACH t IN ARRAY ARRAY['warehouse_accepted_manifests','warehouse_accepted_events','warehouse_outbox',
        'warehouse_execution_intents','warehouse_run_journal','warehouse_reconciliations','warehouse_publications'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_reliability BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION warehouse_reliability_immutable()',t);
    END LOOP;
    FOREACH t IN ARRAY ARRAY['warehouse_accepted_manifests','warehouse_accepted_events','warehouse_outbox',
        'warehouse_execution_intents','warehouse_writer_leases','warehouse_run_journal','warehouse_reconciliations',
        'warehouse_publications','warehouse_publication_heads','warehouse_checkpoints'] LOOP
        EXECUTE format('CREATE TRIGGER no_reliability_truncate BEFORE TRUNCATE ON %I EXECUTE FUNCTION warehouse_no_truncate()',t);
    END LOOP;
END $$;

CREATE FUNCTION warehouse_reliability_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior warehouse_accepted_manifests; s dataset_state_versions; m warehouse_model_sources;
BEGIN
    IF TG_TABLE_NAME='warehouse_accepted_manifests' THEN
        SELECT * INTO m FROM warehouse_model_sources WHERE source_mapping_id=NEW.source_mapping_id FOR UPDATE;
        IF ROW(m.workspace_id,m.warehouse_model_id,m.warehouse_model_version_id,m.source_dataset_id) IS DISTINCT FROM
            ROW(NEW.workspace_id,NEW.warehouse_model_id,NEW.warehouse_model_version_id,NEW.source_dataset_id)
        THEN RAISE EXCEPTION 'Manifest source scope mismatch'; END IF;
        SELECT * INTO s FROM dataset_state_versions WHERE state_id=NEW.source_state_id;
        IF s.status<>'PUBLISHED' OR NOT EXISTS(SELECT 1 FROM delivery_applications a WHERE a.application_id=s.source_application_id
            AND a.status='SUCCESS' AND a.result_state_id=s.state_id) THEN RAISE EXCEPTION 'Published source required'; END IF;
        SELECT * INTO prior FROM warehouse_accepted_manifests WHERE source_mapping_id=NEW.source_mapping_id ORDER BY position DESC LIMIT 1;
        IF NEW.position<>COALESCE(prior.position,0)+1 OR NEW.previous_manifest_id IS DISTINCT FROM prior.manifest_id
            OR s.previous_state_id IS DISTINCT FROM prior.source_state_id
        THEN RAISE EXCEPTION 'Source publication chain gap'; END IF;
        IF NEW.body->>'manifest_id'<>NEW.manifest_id OR NEW.body->>'checksum'<>NEW.checksum
            OR (NEW.body->>'position')::bigint<>NEW.position
            OR (NEW.body->>'source_mapping_id')::bigint<>NEW.source_mapping_id
            OR (NEW.body->>'model_version_id')::bigint<>NEW.warehouse_model_version_id
            OR (NEW.body->'pin'->>'state_id')::bigint<>s.state_id
            OR (NEW.body->'pin'->>'application_id')::bigint<>s.source_application_id
        THEN RAISE EXCEPTION 'Manifest evidence differs'; END IF;
        IF NOT EXISTS(SELECT 1 FROM delivery_applications a JOIN warehouse_model_configurations c
            ON c.warehouse_model_version_id=NEW.warehouse_model_version_id
            WHERE a.application_id=s.source_application_id
            AND c.source_dataset_id=a.dataset_id AND c.source_dataset_version_id=a.dataset_version_id
            AND c.source_policy_id=a.policy_id AND c.source_rule_version=a.applied_rule_version
            AND (NEW.body->'pin'->>'workspace_id')::bigint=NEW.workspace_id
            AND (NEW.body->'pin'->>'dataset_id')::bigint=a.dataset_id
            AND (NEW.body->'pin'->>'dataset_version_id')::bigint=a.dataset_version_id
            AND (NEW.body->'pin'->>'policy_id')::bigint=a.policy_id
            AND (NEW.body->'pin'->>'rule_version')::integer=a.applied_rule_version
            AND (NEW.body->'pin'->>'source_dq_run_id')::bigint=a.source_dq_run_id
            AND NEW.body->'pin'->>'rule_sha256'=c.source_rule_sha256
            AND NEW.body->'pin'->>'artifact_sha256'=s.manifest_sha256)
        THEN RAISE EXCEPTION 'Accepted source contract pin mismatch'; END IF;
    ELSIF TG_TABLE_NAME='warehouse_execution_intents' THEN
        IF NOT EXISTS(SELECT 1 FROM warehouse_operations o JOIN warehouse_resources r ON r.resource_id=o.resource_id
            WHERE o.operation_id=NEW.operation_id AND o.workspace_id=NEW.workspace_id
            AND o.warehouse_model_id=NEW.warehouse_model_id AND o.warehouse_model_version_id=NEW.warehouse_model_version_id
            AND r.resource_id=NEW.resource_id AND r.generation=NEW.generation)
        THEN RAISE EXCEPTION 'Intent operation scope differs'; END IF;
    ELSIF TG_TABLE_NAME='warehouse_publications' THEN
        IF NOT EXISTS(SELECT 1 FROM warehouse_execution_intents i JOIN warehouse_reconciliations r ON r.operation_id=i.operation_id
            WHERE i.operation_id=NEW.operation_id AND i.resource_id=NEW.resource_id
            AND r.reconciliation_id=NEW.reconciliation_id AND r.passed
            AND EXISTS(SELECT 1 FROM warehouse_writer_leases l WHERE l.resource_id=i.resource_id
                AND l.operation_id=i.operation_id AND l.warehouse_run_id=NEW.warehouse_run_id
                AND l.fence=r.fence AND l.expires_at>clock_timestamp()))
        THEN RAISE EXCEPTION 'Reconciled active writer required'; END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accepted_manifest_guard BEFORE INSERT ON warehouse_accepted_manifests FOR EACH ROW EXECUTE FUNCTION warehouse_reliability_insert_guard();
CREATE TRIGGER execution_intent_guard BEFORE INSERT ON warehouse_execution_intents FOR EACH ROW EXECUTE FUNCTION warehouse_reliability_insert_guard();
CREATE TRIGGER warehouse_publication_guard BEFORE INSERT ON warehouse_publications FOR EACH ROW EXECUTE FUNCTION warehouse_reliability_insert_guard();

-- Deferred proof checks the final transaction state, so a crash/rollback cannot
-- leave a checkpoint ahead of its complete immutable publication vector.
CREATE FUNCTION warehouse_progress_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p warehouse_publications; v JSONB; rid BIGINT;
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Progress history cannot be removed'; END IF;
    rid:=NEW.resource_id;
    SELECT p1.* INTO p FROM warehouse_publications p1 JOIN warehouse_publication_heads h
        ON h.publication_id=p1.publication_id WHERE h.resource_id=rid;
    IF p.publication_id IS NULL THEN RAISE EXCEPTION 'Published head required'; END IF;
    IF NOT EXISTS(SELECT 1 FROM warehouse_runs WHERE warehouse_run_id=p.warehouse_run_id AND status='SUCCEEDED')
        THEN RAISE EXCEPTION 'Publication head requires finalized successful run'; END IF;
    IF NOT EXISTS(SELECT 1 FROM warehouse_publication_heads WHERE resource_id=rid AND revision=p.revision)
        THEN RAISE EXCEPTION 'Head revision mismatch'; END IF;
    FOR v IN SELECT value FROM jsonb_array_elements(p.source_vector) LOOP
        IF NOT EXISTS(SELECT 1 FROM warehouse_checkpoints c WHERE c.resource_id=rid
            AND c.source_mapping_id=(v->>'mapping_id')::bigint AND c.position=(v->>'position')::bigint
            AND c.publication_id=p.publication_id)
        THEN RAISE EXCEPTION 'Publication/checkpoint vector mismatch'; END IF;
    END LOOP;
    IF (SELECT count(*) FROM warehouse_checkpoints WHERE resource_id=rid)<>jsonb_array_length(p.source_vector)
        THEN RAISE EXCEPTION 'Incomplete publication vector'; END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER warehouse_head_consistent AFTER INSERT OR UPDATE OR DELETE ON warehouse_publication_heads
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION warehouse_progress_consistent();
CREATE CONSTRAINT TRIGGER warehouse_checkpoint_consistent AFTER INSERT OR UPDATE OR DELETE ON warehouse_checkpoints
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION warehouse_progress_consistent();

CREATE FUNCTION warehouse_reliability_mutation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p warehouse_publications;
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Progress/lease identity cannot be deleted'; END IF;
    IF TG_TABLE_NAME='warehouse_writer_leases' THEN
        IF NOT EXISTS(SELECT 1 FROM warehouse_runs r JOIN warehouse_execution_intents i USING(operation_id)
            WHERE r.warehouse_run_id=NEW.warehouse_run_id AND r.operation_id=NEW.operation_id AND i.resource_id=NEW.resource_id)
        THEN RAISE EXCEPTION 'Lease run/operation scope mismatch'; END IF;
        IF TG_OP='UPDATE' AND (NEW.resource_id<>OLD.resource_id OR NEW.fence<OLD.fence
            OR (NEW.fence=OLD.fence AND ROW(NEW.operation_id,NEW.warehouse_run_id,NEW.worker)
                IS DISTINCT FROM ROW(OLD.operation_id,OLD.warehouse_run_id,OLD.worker)))
        THEN RAISE EXCEPTION 'Invalid fencing transition'; END IF;
    ELSIF TG_TABLE_NAME='warehouse_checkpoints' THEN
        IF NOT EXISTS(SELECT 1 FROM warehouse_resources r JOIN warehouse_model_sources s
            ON s.workspace_id=r.workspace_id AND s.warehouse_model_id=r.warehouse_model_id
            AND s.warehouse_model_version_id=r.warehouse_model_version_id
            WHERE r.resource_id=NEW.resource_id AND s.source_mapping_id=NEW.source_mapping_id)
        THEN RAISE EXCEPTION 'Checkpoint source/model/resource scope mismatch'; END IF;
        IF TG_OP='INSERT' AND NEW.version<>1 THEN RAISE EXCEPTION 'Initial checkpoint CAS version must be one'; END IF;
        IF TG_OP='UPDATE' AND (ROW(NEW.resource_id,NEW.source_mapping_id) IS DISTINCT FROM ROW(OLD.resource_id,OLD.source_mapping_id)
            OR NEW.position<OLD.position OR NEW.version<>OLD.version+1)
        THEN RAISE EXCEPTION 'Checkpoint CAS version must advance without rewind'; END IF;
    ELSIF TG_TABLE_NAME='warehouse_publication_heads' THEN
        SELECT * INTO p FROM warehouse_publications WHERE publication_id=NEW.publication_id;
        IF TG_OP='UPDATE' THEN
            IF NEW.resource_id<>OLD.resource_id OR NEW.revision<>OLD.revision+1 OR p.previous_publication_id IS DISTINCT FROM OLD.publication_id
            THEN RAISE EXCEPTION 'Publication head must advance from exact predecessor'; END IF;
        ELSIF NEW.revision<>1 OR p.previous_publication_id IS NOT NULL THEN
            RAISE EXCEPTION 'Initial publication head must start at one';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER warehouse_lease_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_writer_leases FOR EACH ROW EXECUTE FUNCTION warehouse_reliability_mutation_guard();
CREATE TRIGGER warehouse_checkpoint_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_checkpoints FOR EACH ROW EXECUTE FUNCTION warehouse_reliability_mutation_guard();
CREATE TRIGGER warehouse_head_guard BEFORE INSERT OR UPDATE OR DELETE ON warehouse_publication_heads FOR EACH ROW EXECUTE FUNCTION warehouse_reliability_mutation_guard();

CREATE FUNCTION warehouse_evidence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m warehouse_accepted_manifests; i warehouse_execution_intents;
BEGIN
    IF TG_TABLE_NAME='warehouse_accepted_events' THEN
        SELECT * INTO m FROM warehouse_accepted_manifests WHERE manifest_id=NEW.manifest_id;
        IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(m.body->'events') e WHERE e->>'event_id'=NEW.event_id
            AND e->>'checksum'=NEW.checksum AND e->>'outcome'=NEW.outcome AND e->'payload'=NEW.payload)
        THEN RAISE EXCEPTION 'Event differs from immutable manifest'; END IF;
    ELSIF TG_TABLE_NAME='warehouse_outbox' THEN
        SELECT * INTO m FROM warehouse_accepted_manifests WHERE manifest_id=NEW.manifest_id;
        IF (SELECT count(*) FROM warehouse_accepted_events WHERE manifest_id=NEW.manifest_id)<>(m.body->>'event_count')::bigint
        THEN RAISE EXCEPTION 'Outbox requires complete event set'; END IF;
    ELSIF TG_TABLE_NAME='warehouse_reconciliations' THEN
        SELECT * INTO i FROM warehouse_execution_intents WHERE operation_id=NEW.operation_id;
        IF NOT EXISTS(SELECT 1 FROM warehouse_runs WHERE warehouse_run_id=NEW.warehouse_run_id AND operation_id=NEW.operation_id)
        THEN RAISE EXCEPTION 'Reconciliation run scope differs'; END IF;
        IF NEW.passed AND (NEW.evidence->'receipt'->>'outcome' IS DISTINCT FROM 'SUCCEEDED'
            OR NEW.evidence->'receipt'->'result' IS DISTINCT FROM i.body->'expected'
            OR NEW.evidence->'receipt'->>'application_token' IS DISTINCT FROM i.application_token
            OR NEW.evidence->'receipt'->>'intent_sha256' IS DISTINCT FROM i.intent_sha256)
        THEN RAISE EXCEPTION 'Reconciliation result does not prove expected intent'; END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accepted_event_guard BEFORE INSERT ON warehouse_accepted_events FOR EACH ROW EXECUTE FUNCTION warehouse_evidence_guard();
CREATE TRIGGER outbox_evidence_guard BEFORE INSERT ON warehouse_outbox FOR EACH ROW EXECUTE FUNCTION warehouse_evidence_guard();
CREATE TRIGGER reconciliation_evidence_guard BEFORE INSERT ON warehouse_reconciliations FOR EACH ROW EXECUTE FUNCTION warehouse_evidence_guard();
COMMIT;
