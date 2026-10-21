-- Optional empty-only rollback. Prefer disabling the feature and retaining history.
BEGIN;
LOCK TABLE warehouse_models,warehouse_model_versions,warehouse_model_sources,
    warehouse_resources,warehouse_operations,warehouse_runs IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM warehouse_models) OR EXISTS(SELECT 1 FROM warehouse_model_versions)
        OR EXISTS(SELECT 1 FROM warehouse_model_sources) OR EXISTS(SELECT 1 FROM warehouse_resources)
        OR EXISTS(SELECT 1 FROM warehouse_operations) OR EXISTS(SELECT 1 FROM warehouse_runs) THEN
        RAISE EXCEPTION 'Warehouse rollback requires empty metadata; retain history and disable the feature';
    END IF;
END $$;
DROP TABLE warehouse_runs,warehouse_operations,warehouse_resources,warehouse_model_sources,warehouse_model_versions,warehouse_models;
DROP FUNCTION warehouse_guard_history(),warehouse_guard_sources(),warehouse_guard_insert(),warehouse_no_truncate();
COMMIT;
