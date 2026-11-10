-- 414C3b: PostgreSQL-only publication retirement fence.
-- A retired publication is metadata-ineligible for NEW query links.
-- This does NOT delete provider tables or authorize cleanup/dispatch.
BEGIN;

CREATE TABLE warehouse_query_publication_retirement_fences (
    resource_id BIGINT NOT NULL,
    publication_id BIGINT NOT NULL,
    fenced_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (resource_id,publication_id),
    FOREIGN KEY (resource_id,publication_id)
        REFERENCES warehouse_publications(resource_id,publication_id)
);

-- Lock order for the affected resource IDs is ascending in both paths.
-- The per-resource advisory transaction lock serializes retirement with
-- link creation even when no preexisting pin/reservation rows are lockable.
CREATE FUNCTION warehouse_query_retirement_fence_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Warehouse publication retirement fence is immutable';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(
        'datarise-publication-retirement:' || NEW.resource_id::text,0
    ));

    IF EXISTS(
        SELECT 1 FROM warehouse_publication_heads h
        WHERE h.resource_id=NEW.resource_id
          AND h.publication_id=NEW.publication_id
    ) THEN
        RAISE EXCEPTION 'Current publication cannot be retirement fenced';
    END IF;

    IF warehouse_query_publication_dependency_present(
        NEW.resource_id,NEW.publication_id
    ) THEN
        RAISE EXCEPTION 'Active query publication dependency blocks retirement';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER warehouse_query_retirement_fence_validate
BEFORE INSERT OR UPDATE OR DELETE
ON warehouse_query_publication_retirement_fences
FOR EACH ROW EXECUTE FUNCTION warehouse_query_retirement_fence_guard();

CREATE TRIGGER warehouse_query_retirement_fence_no_truncate
BEFORE TRUNCATE ON warehouse_query_publication_retirement_fences
FOR EACH STATEMENT EXECUTE FUNCTION query_governance_no_truncate();

-- This trigger alphabetically precedes the existing
-- warehouse_query_attempt_reservation_link_validate BEFORE INSERT trigger.
-- It locks all pinned resources and denies links to a fenced snapshot.
CREATE FUNCTION warehouse_query_retirement_link_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    pin RECORD;
BEGIN
    FOR pin IN
        SELECT p.resource_id,p.publication_id
        FROM warehouse_query_attempt_publication_pins p
        WHERE p.attempt_id=NEW.attempt_id
        ORDER BY p.resource_id
    LOOP
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'datarise-publication-retirement:' || pin.resource_id::text,0
        ));
        IF EXISTS(
            SELECT 1 FROM warehouse_query_publication_retirement_fences f
            WHERE f.resource_id=pin.resource_id
              AND f.publication_id=pin.publication_id
        ) THEN
            RAISE EXCEPTION 'Retired publication cannot acquire query dependency';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE TRIGGER a_warehouse_query_retirement_link_validate
BEFORE INSERT ON warehouse_query_attempt_reservation_links
FOR EACH ROW EXECUTE FUNCTION warehouse_query_retirement_link_guard();

COMMIT;
