-- =========================================================
-- Migration 0002: dataset-version-scoped rules/DQ/Gold
-- =========================================================
-- Applied manually via pgAdmin on 2026-09-26 against the live
-- development database. Recorded here for history/reproducibility.
--
-- All additions are nullable. Nothing existing was renamed, dropped,
-- or made required. workspaces/datasets/dataset_versions were empty
-- at the time this ran, so no backfill of existing business_rules/
-- business_rule_answers/data_quality_runs/gold_runs rows was
-- possible or necessary - they simply have NULL in the new columns
-- until they are reprocessed through a workspace/dataset-aware path.

-- 1. Cache the Bronze-computed logical schema fingerprint on the
--    physical file itself (content-deterministic, so file-scoped
--    is correct here).
ALTER TABLE physical_files
    ADD COLUMN IF NOT EXISTS schema_hash VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_physical_files_schema_hash
    ON physical_files(schema_hash);


-- 2. business_rules: shared per dataset_version (schema), not per
--    physical file, once a workspace/dataset context is known.
ALTER TABLE business_rules
    ADD COLUMN IF NOT EXISTS dataset_version_id BIGINT;

ALTER TABLE business_rules
    ADD CONSTRAINT fk_business_rules_dataset_version
    FOREIGN KEY (dataset_version_id)
    REFERENCES dataset_versions(dataset_version_id);

CREATE INDEX IF NOT EXISTS idx_business_rules_dataset_version_id
    ON business_rules(dataset_version_id);

-- NULLs are not considered equal by UNIQUE, so this coexists safely
-- with existing file-scoped rows (dataset_version_id IS NULL).
ALTER TABLE business_rules
    ADD CONSTRAINT uq_business_rule_dataset_version_column_type
    UNIQUE (dataset_version_id, column_name, rule_type);


-- 3. business_rule_answers: same reasoning as business_rules.
ALTER TABLE business_rule_answers
    ADD COLUMN IF NOT EXISTS dataset_version_id BIGINT;

ALTER TABLE business_rule_answers
    ADD CONSTRAINT fk_business_rule_answers_dataset_version
    FOREIGN KEY (dataset_version_id)
    REFERENCES dataset_versions(dataset_version_id);

CREATE INDEX IF NOT EXISTS idx_business_rule_answers_dataset_version_id
    ON business_rule_answers(dataset_version_id);

ALTER TABLE business_rule_answers
    ADD CONSTRAINT uq_business_rule_answer_dataset_version_column_type
    UNIQUE (dataset_version_id, column_name, rule_type);


-- 4. data_quality_runs: results belong to one physical file's data,
--    but we record which dataset_version_file link that file was
--    processed under, so lineage is traceable.
ALTER TABLE data_quality_runs
    ADD COLUMN IF NOT EXISTS dataset_version_file_id BIGINT;

ALTER TABLE data_quality_runs
    ADD CONSTRAINT fk_data_quality_runs_dataset_version_file
    FOREIGN KEY (dataset_version_file_id)
    REFERENCES dataset_version_files(dataset_version_file_id);

CREATE INDEX IF NOT EXISTS idx_data_quality_runs_dataset_version_file_id
    ON data_quality_runs(dataset_version_file_id);


-- 5. gold_runs: same reasoning as data_quality_runs.
ALTER TABLE gold_runs
    ADD COLUMN IF NOT EXISTS dataset_version_file_id BIGINT;

ALTER TABLE gold_runs
    ADD CONSTRAINT fk_gold_runs_dataset_version_file
    FOREIGN KEY (dataset_version_file_id)
    REFERENCES dataset_version_files(dataset_version_file_id);

CREATE INDEX IF NOT EXISTS idx_gold_runs_dataset_version_file_id
    ON gold_runs(dataset_version_file_id);


-- 6. processing_attempts: lets SILVER/GOLD attempts record which
--    dataset_version_file they ran against (BRONZE attempts predate
--    version resolution, so this stays NULL for BRONZE).
ALTER TABLE processing_attempts
    ADD COLUMN IF NOT EXISTS dataset_version_file_id BIGINT;

ALTER TABLE processing_attempts
    ADD CONSTRAINT fk_processing_attempts_dataset_version_file
    FOREIGN KEY (dataset_version_file_id)
    REFERENCES dataset_version_files(dataset_version_file_id);

CREATE INDEX IF NOT EXISTS idx_processing_attempts_dataset_version_file_id
    ON processing_attempts(dataset_version_file_id);
