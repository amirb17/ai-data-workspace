-- Empty-only rollback for Phase 11H query-governance metadata.
BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM query_governance_reservations LIMIT 1
    ) OR EXISTS (
        SELECT 1 FROM query_governance_usage LIMIT 1
    ) THEN
        RAISE EXCEPTION
            'Refusing query-governance rollback with retained history';
    END IF;
END
$$;

DROP TABLE query_governance_usage;
DROP TABLE query_governance_reservations;

DROP FUNCTION query_governance_completion_consistent();
DROP FUNCTION query_governance_no_truncate();
DROP FUNCTION query_governance_reservation_no_delete();
DROP FUNCTION query_governance_usage_guard();
DROP FUNCTION query_governance_reservation_guard();

COMMIT;
