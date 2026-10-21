-- Empty-only rollback; never discard approved semantic history.
BEGIN;
LOCK TABLE warehouse_model_configurations IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM warehouse_model_configurations) THEN RAISE EXCEPTION 'Retain configuration history; rollback requires empty metadata'; END IF;
END $$;
DROP TRIGGER warehouse_modeling_approval ON warehouse_model_versions;
DROP TABLE warehouse_model_configurations;
DROP FUNCTION warehouse_modeling_guard(),warehouse_modeling_approval_guard();
COMMIT;
