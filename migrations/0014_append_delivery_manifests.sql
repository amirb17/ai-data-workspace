-- Delivery snapshots are immutable; application retries consume their pinned bytes.
CREATE TABLE delivery_manifests (
    manifest_id BIGSERIAL PRIMARY KEY,
    application_id BIGINT NOT NULL UNIQUE REFERENCES delivery_applications(application_id),
    manifest_version INTEGER NOT NULL DEFAULT 1 CHECK(manifest_version=1),
    status TEXT NOT NULL DEFAULT 'READY_TO_APPLY' CHECK(status='READY_TO_APPLY'),
    manifest_key TEXT NOT NULL UNIQUE,
    manifest_sha256 VARCHAR(64) NOT NULL,
    silver_key TEXT NOT NULL UNIQUE,
    silver_sha256 VARCHAR(64) NOT NULL,
    effective_schema JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE FUNCTION immutable_delivery_manifest() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Delivery manifests are immutable'; END $$;
CREATE TRIGGER protect_delivery_manifest BEFORE UPDATE OR DELETE ON delivery_manifests
    FOR EACH ROW EXECUTE FUNCTION immutable_delivery_manifest();
ALTER TABLE delivery_applications ADD COLUMN incremental_rejected_rows BIGINT CHECK(incremental_rejected_rows>=0);
ALTER TABLE datasets ADD COLUMN state_analytics_status TEXT CHECK(state_analytics_status='STALE');
