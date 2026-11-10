-- Step 414C3a: immutable association between a selected publication snapshot
-- and a durable governed SQL reservation. Metadata only: no BigQuery IO.
BEGIN;

CREATE TABLE warehouse_query_attempt_reservation_links (
    attempt_id BIGINT PRIMARY KEY
        REFERENCES warehouse_query_attempt_registry(attempt_id),
    reservation_id BIGINT NOT NULL UNIQUE
        REFERENCES query_governance_reservations(reservation_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION warehouse_query_attempt_reservation_link_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    a warehouse_query_attempt_registry%ROWTYPE;
    r query_governance_reservations%ROWTYPE;
    pins_count INTEGER;
    valid_count INTEGER;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Warehouse query reservation link is immutable';
    END IF;
    SELECT * INTO a FROM warehouse_query_attempt_registry
        WHERE attempt_id=NEW.attempt_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Registered warehouse attempt required';
    END IF;
    SELECT * INTO r FROM query_governance_reservations
        WHERE reservation_id=NEW.reservation_id FOR UPDATE;
    IF NOT FOUND OR r.workspace_id<>a.workspace_id
        OR r.owner_key<>a.owner_key
        OR r.request_token<>btrim(a.request_token)
        OR r.consumer<>'SQL'
        OR r.provider<>'BIGQUERY'
        OR r.status<>'ACTIVE'
        OR r.expires_at<=clock_timestamp()
    THEN
        RAISE EXCEPTION 'Matching owned ACTIVE SQL reservation required';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM workspaces
        WHERE workspace_id=a.workspace_id AND owner=a.owner_key
    ) THEN
        RAISE EXCEPTION 'Current attempt ownership required';
    END IF;

    SELECT count(*) INTO pins_count
    FROM warehouse_query_attempt_publication_pins
    WHERE attempt_id=a.attempt_id;
    IF pins_count NOT BETWEEN 1 AND 2 THEN
        RAISE EXCEPTION 'One or two pinned publications required';
    END IF;

    -- A publication advancing before link registration invalidates this
    -- candidate. Lock heads for the duration of this transaction.
    -- Lock first, THEN validate. Checking freshness before taking the
    -- lock would leave a TOCTOU window while waiting for the writer.
    PERFORM h.resource_id
    FROM warehouse_publication_heads h
    JOIN warehouse_query_attempt_publication_pins p
      ON p.resource_id=h.resource_id
    WHERE p.attempt_id=a.attempt_id
    ORDER BY h.resource_id
    FOR SHARE OF h;

    SELECT count(*) INTO valid_count
    FROM warehouse_query_attempt_publication_pins p
    JOIN warehouse_publication_heads h
       ON h.resource_id=p.resource_id
      AND h.publication_id=p.publication_id
      AND h.revision=p.publication_revision
    JOIN warehouse_resources w ON w.resource_id=p.resource_id
    WHERE p.attempt_id=a.attempt_id
      AND w.workspace_id=a.workspace_id
      AND w.status='REGISTERED'
      AND w.environment='development';
    IF valid_count<>pins_count THEN
        RAISE EXCEPTION 'Current publication pin set required';
    END IF;

    RETURN NEW;
END $$;

-- Once an attempt is linked to a reservation, forbid adding extra pinned
-- models: the pin set used to establish authorization must stay fixed.
CREATE FUNCTION warehouse_query_linked_pin_insert_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM warehouse_query_attempt_registry
      WHERE attempt_id=NEW.attempt_id FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM warehouse_query_attempt_reservation_links
        WHERE attempt_id=NEW.attempt_id
    ) THEN
        RAISE EXCEPTION 'Linked publication pin set is immutable';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER warehouse_query_linked_pin_insert_validate
BEFORE INSERT ON warehouse_query_attempt_publication_pins
FOR EACH ROW EXECUTE FUNCTION warehouse_query_linked_pin_insert_guard();

CREATE TRIGGER warehouse_query_attempt_reservation_link_validate
BEFORE INSERT OR UPDATE OR DELETE
ON warehouse_query_attempt_reservation_links
FOR EACH ROW EXECUTE FUNCTION warehouse_query_attempt_reservation_link_guard();

CREATE TRIGGER warehouse_query_attempt_reservation_link_no_truncate
BEFORE TRUNCATE ON warehouse_query_attempt_reservation_links
FOR EACH STATEMENT EXECUTE FUNCTION query_governance_no_truncate();

-- Read-only conservative cleanup decision. Callers must recheck this under
-- a future atomic cleanup fence; this function DOES NOT authorize deletion.
CREATE FUNCTION warehouse_query_publication_dependency_present(
    target_resource_id BIGINT, target_publication_id BIGINT
) RETURNS BOOLEAN LANGUAGE sql VOLATILE AS $$
    SELECT EXISTS (
        SELECT 1
        FROM warehouse_query_attempt_publication_pins p
        JOIN warehouse_query_attempt_reservation_links l
          ON l.attempt_id=p.attempt_id
        JOIN query_governance_reservations r
          ON r.reservation_id=l.reservation_id
        WHERE p.resource_id=target_resource_id
          AND p.publication_id=target_publication_id
          AND (
              r.status='EXECUTING'
              OR (
                  r.status='ACTIVE'
                  AND r.expires_at>clock_timestamp()
              )
          )
    )
$$;

COMMIT;
