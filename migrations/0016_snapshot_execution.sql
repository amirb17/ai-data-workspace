-- Phase 6D: explicit, immutable snapshot policy/delivery context and outcomes.
BEGIN;
ALTER TABLE dataset_load_policies ADD COLUMN snapshot_coverage TEXT
    CHECK (snapshot_coverage IN ('COMPLETE','PARTIAL'));
-- Existing design-only SNAPSHOT policies are conservative, never inferred complete.
ALTER TABLE dataset_load_policies DISABLE TRIGGER immutable_load_policy;
UPDATE dataset_load_policies SET snapshot_coverage='PARTIAL' WHERE load_strategy='SNAPSHOT';
ALTER TABLE dataset_load_policies ENABLE TRIGGER immutable_load_policy;
ALTER TABLE dataset_load_policies ADD CONSTRAINT snapshot_policy_coverage
    CHECK ((load_strategy='SNAPSHOT') = (snapshot_coverage IS NOT NULL));
CREATE TABLE snapshot_delivery_contexts (
    upload_request_id BIGINT PRIMARY KEY REFERENCES upload_requests(upload_id),
    coverage TEXT NOT NULL CHECK (coverage IN ('COMPLETE','PARTIAL')),
    effective_at TIMESTAMPTZ,
    delivery_kind TEXT NOT NULL CHECK (delivery_kind IN ('NORMAL','CORRECTION','BACKFILL')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE FUNCTION immutable_snapshot_context() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Snapshot delivery declarations are immutable'; END $$;
CREATE TRIGGER protect_snapshot_context BEFORE UPDATE OR DELETE ON snapshot_delivery_contexts
    FOR EACH ROW EXECUTE FUNCTION immutable_snapshot_context();
ALTER TABLE delivery_applications ADD COLUMN snapshot_coverage TEXT CHECK (snapshot_coverage IN ('COMPLETE','PARTIAL'));
ALTER TABLE delivery_applications ADD COLUMN snapshot_effective_at TIMESTAMPTZ;
ALTER TABLE delivery_applications ADD COLUMN snapshot_boundary_at TIMESTAMPTZ;
ALTER TABLE delivery_applications ADD COLUMN delivery_kind TEXT CHECK (delivery_kind IN ('NORMAL','CORRECTION','BACKFILL'));
ALTER TABLE delivery_applications ADD COLUMN snapshot_outcome TEXT CHECK (snapshot_outcome IN ('APPLIED','STALE','EQUAL_TIME_CONFLICT','DEACTIVATION_WITHHELD'));
ALTER TABLE delivery_applications ADD COLUMN reactivated_rows BIGINT CHECK (reactivated_rows>=0);
ALTER TABLE delivery_applications ADD COLUMN active_rows BIGINT CHECK (active_rows>=0);
ALTER TABLE delivery_applications ADD COLUMN inactive_rows BIGINT CHECK (inactive_rows>=0);
CREATE FUNCTION protect_snapshot_application_context() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.snapshot_coverage,NEW.snapshot_effective_at,NEW.delivery_kind)
       IS DISTINCT FROM ROW(OLD.snapshot_coverage,OLD.snapshot_effective_at,OLD.delivery_kind)
    THEN RAISE EXCEPTION 'Snapshot application context is immutable'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_snapshot_application_context BEFORE UPDATE ON delivery_applications
    FOR EACH ROW EXECUTE FUNCTION protect_snapshot_application_context();
COMMIT;
