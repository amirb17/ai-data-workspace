-- Empty-only rollback. Never discard serving-publication evidence.

BEGIN;

LOCK TABLE warehouse_serving_materializations
IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM warehouse_serving_materializations
        LIMIT 1
    ) THEN
        RAISE EXCEPTION
            'Serving materialization rollback requires empty metadata';
    END IF;
END
$$;

DROP TRIGGER
    warehouse_serving_materialization_no_truncate
ON warehouse_serving_materializations;

DROP TRIGGER
    warehouse_serving_materialization_validate
ON warehouse_serving_materializations;

DROP TABLE warehouse_serving_materializations;

DROP FUNCTION warehouse_serving_materialization_guard();

COMMIT;