-- Allow explicitly versioned exact-decimal delivery manifests.
-- Existing V1 records remain unchanged.

ALTER TABLE delivery_manifests
DROP CONSTRAINT IF EXISTS delivery_manifests_manifest_version_check;

ALTER TABLE delivery_manifests
ADD CONSTRAINT delivery_manifests_manifest_version_check
CHECK (manifest_version IN (1, 2));


-- Reserved for Silver-source integrity evidence.
-- Historical rows remain NULL and are not backfilled.

ALTER TABLE data_quality_runs
ADD COLUMN silver_sha256 VARCHAR(64)
CHECK (
    silver_sha256 IS NULL
    OR silver_sha256 ~ '^[a-f0-9]{64}$'
);