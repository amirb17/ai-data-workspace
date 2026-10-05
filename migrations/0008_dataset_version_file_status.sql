-- The live development schema already has this column, but the recorded
-- baseline omitted it. Make new installations reproduce the existing lifecycle.
ALTER TABLE dataset_version_files
    ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'UPLOADED';

-- Dataset-version-scoped repository writes deliberately omit legacy file_id.
-- Migration 0002 added their identity, but did not relax these legacy columns.
ALTER TABLE business_rules ALTER COLUMN file_id DROP NOT NULL;
ALTER TABLE business_rule_answers ALTER COLUMN file_id DROP NOT NULL;
