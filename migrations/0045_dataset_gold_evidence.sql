-- Proposed once-only additive evidence, after 0044. No runtime grants/activation.
-- Existing dataset_gold_runs and datasets.current_gold_run_id remain authority.
BEGIN;
CREATE TABLE dataset_gold_evidence (
 verification_id BIGSERIAL PRIMARY KEY,
 gold_run_id BIGINT NOT NULL REFERENCES dataset_gold_runs(gold_run_id),
 contract_version INTEGER NOT NULL CHECK(contract_version=1),
 source_fingerprint CHAR(64) NOT NULL CHECK(source_fingerprint ~ '^[a-f0-9]{64}$'),
 source_contract JSONB NOT NULL CHECK(jsonb_typeof(source_contract)='object' AND octet_length(source_contract::text)<=4194304),
 manifest_key TEXT NOT NULL,
 manifest_sha256 CHAR(64) NOT NULL CHECK(manifest_sha256 ~ '^[a-f0-9]{64}$'),
 catalog_sha256 CHAR(64) NOT NULL CHECK(catalog_sha256 ~ '^[a-f0-9]{64}$'),
 verified_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(gold_run_id,source_fingerprint)
);
CREATE TRIGGER immutable_dataset_gold_evidence BEFORE UPDATE OR DELETE ON dataset_gold_evidence
 FOR EACH ROW EXECUTE FUNCTION protect_processing_stage_receipt();
CREATE TRIGGER no_truncate_dataset_gold_evidence BEFORE TRUNCATE ON dataset_gold_evidence
 EXECUTE FUNCTION warehouse_no_truncate();
REVOKE ALL ON dataset_gold_evidence FROM PUBLIC;
CREATE FUNCTION dataset_gold_evidence_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE g dataset_gold_runs;
BEGIN
 SELECT * INTO g FROM dataset_gold_runs WHERE gold_run_id=NEW.gold_run_id;
 IF (NEW.source_contract->>'version')::integer IS DISTINCT FROM 1
 OR (NEW.source_contract->>'dataset_id')::bigint IS DISTINCT FROM g.dataset_id
 OR (NEW.source_contract->>'state_id')::bigint IS DISTINCT FROM g.source_state_id
 OR NOT EXISTS(SELECT 1 FROM datasets d WHERE d.dataset_id=g.dataset_id
   AND d.workspace_id=(NEW.source_contract->>'workspace_id')::bigint)
 OR jsonb_typeof(NEW.source_contract->'history') IS DISTINCT FROM 'array'
 OR jsonb_array_length(NEW.source_contract->'history') NOT BETWEEN 1 AND 64
 OR (g.status='SUCCESS' AND (NEW.manifest_key IS DISTINCT FROM g.manifest_key OR NEW.manifest_sha256 IS DISTINCT FROM g.manifest_sha256))
 THEN RAISE EXCEPTION 'Dataset Gold evidence scope mismatch'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER dataset_gold_evidence_scope BEFORE INSERT ON dataset_gold_evidence
 FOR EACH ROW EXECUTE FUNCTION dataset_gold_evidence_scope();
REVOKE ALL ON FUNCTION dataset_gold_evidence_scope() FROM PUBLIC;
DO $$ DECLARE trusted TEXT:=current_schema(); BEGIN
 EXECUTE format('ALTER FUNCTION dataset_gold_evidence_scope() SET search_path = pg_catalog, %I, pg_temp',trusted);
END $$;
COMMIT;
