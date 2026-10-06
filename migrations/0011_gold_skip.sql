-- Intentional terminal DQ outcome, independent of processing attempts.
ALTER TABLE dataset_version_files ADD COLUMN IF NOT EXISTS gold_skip_reason TEXT;
ALTER TABLE dataset_version_files ADD COLUMN IF NOT EXISTS gold_skipped_at TIMESTAMPTZ;
