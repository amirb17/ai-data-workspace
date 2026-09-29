-- =========================================================
-- Migration 0003: dataset-version rule versioning
-- =========================================================
-- Business-rule configuration is scoped to dataset_versions.
-- rule_version increments when the effective rule configuration
-- changes, allowing Silver/Gold outputs to be versioned and
-- reprocessed independently for each dataset version.

ALTER TABLE dataset_versions
    ADD COLUMN IF NOT EXISTS rule_version INTEGER NOT NULL DEFAULT 0;
