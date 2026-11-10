-- 414C1: publication selection snapshot for each server-owned query attempt.
-- Captures a CURRENT published head at INSERT time. It does NOT retain the
-- provider table, authorize dispatch, or install a revocation fence.
BEGIN;

CREATE TABLE warehouse_query_attempt_publication_pins (
    attempt_id BIGINT NOT NULL
        REFERENCES warehouse_query_attempt_registry(attempt_id),
    resource_id BIGINT NOT NULL,
    publication_id BIGINT NOT NULL,
    publication_revision BIGINT NOT NULL CHECK(publication_revision>0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(attempt_id,resource_id),
    FOREIGN KEY(resource_id,publication_id)
        REFERENCES warehouse_publications(resource_id,publication_id)
);

CREATE INDEX warehouse_query_attempt_publication_lookup
    ON warehouse_query_attempt_publication_pins(resource_id,publication_id);

CREATE FUNCTION warehouse_query_attempt_publication_guard()
RETURNS trigger LANGUAGE plpgsql
AS $$
DECLARE
    attempt_row warehouse_query_attempt_registry%ROWTYPE;
    resource_row warehouse_resources%ROWTYPE;
    publication_row warehouse_publications%ROWTYPE;
    head_row warehouse_publication_heads%ROWTYPE;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Warehouse query publication pin is immutable';
    END IF;

    -- The attempt is the ownership anchor, not arbitrary caller-supplied
    -- model or workspace IDs. Row locks serialize the ownership read.
    SELECT * INTO attempt_row
      FROM warehouse_query_attempt_registry
      WHERE attempt_id=NEW.attempt_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Registered warehouse query attempt required';
    END IF;
    IF NOT EXISTS(
        SELECT 1 FROM workspaces
        WHERE workspace_id=attempt_row.workspace_id
          AND owner=attempt_row.owner_key
    ) THEN
        RAISE EXCEPTION 'Warehouse query attempt owner no longer current';
    END IF;

    SELECT * INTO resource_row FROM warehouse_resources
      WHERE resource_id=NEW.resource_id FOR SHARE;
    IF NOT FOUND OR resource_row.workspace_id<>attempt_row.workspace_id
       OR resource_row.status<>'REGISTERED'
       OR resource_row.environment<>'development'
    THEN
        RAISE EXCEPTION 'Owned active development warehouse resource required';
    END IF;

    -- FOR SHARE prevents a concurrent head UPDATE from slipping through
    -- while this INSERT transaction is committing; it does NOT guarantee
    -- the head remains current after the transaction ends.
    SELECT * INTO head_row FROM warehouse_publication_heads
      WHERE resource_id=NEW.resource_id FOR SHARE;
    SELECT * INTO publication_row FROM warehouse_publications
      WHERE publication_id=NEW.publication_id
        AND resource_id=NEW.resource_id FOR SHARE;
    IF head_row.publication_id IS DISTINCT FROM NEW.publication_id
       OR head_row.revision IS DISTINCT FROM NEW.publication_revision
       OR publication_row.publication_id IS NULL
       OR publication_row.revision IS DISTINCT FROM NEW.publication_revision
    THEN
        RAISE EXCEPTION 'Current warehouse publication head required';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER warehouse_query_attempt_publication_validate
BEFORE INSERT OR UPDATE OR DELETE
ON warehouse_query_attempt_publication_pins
FOR EACH ROW
EXECUTE FUNCTION warehouse_query_attempt_publication_guard();

CREATE TRIGGER warehouse_query_attempt_publication_no_truncate
BEFORE TRUNCATE ON warehouse_query_attempt_publication_pins
FOR EACH STATEMENT EXECUTE FUNCTION query_governance_no_truncate();

COMMIT;
