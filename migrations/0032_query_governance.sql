-- Durable analytical-query budget/concurrency reservations.
-- Additive control-plane metadata only. No consumer migration or query execution.
BEGIN;

CREATE TABLE query_governance_reservations (
    reservation_id BIGSERIAL PRIMARY KEY,

    request_token TEXT NOT NULL UNIQUE
        CHECK(request_token ~ '^[a-f0-9]{64}$'),

    intent_sha256 TEXT NOT NULL
        CHECK(intent_sha256 ~ '^[a-f0-9]{64}$'),

    workspace_id BIGINT NOT NULL REFERENCES workspaces(workspace_id),
    owner_key TEXT NOT NULL REFERENCES users(owner_key),

    consumer TEXT NOT NULL
        CHECK(consumer IN ('SQL','ANALYTICS','ASK','METRIC')),

    provider TEXT NOT NULL
        CHECK(provider IN ('DUCKDB','BIGQUERY')),

    estimated_bytes BIGINT NOT NULL
        CHECK(estimated_bytes >= 0),

    estimate_confidence TEXT NOT NULL
        CHECK(estimate_confidence IN ('EXACT','UPPER_BOUND')),

    reserved_bytes BIGINT NOT NULL
        CHECK(reserved_bytes >= 0 AND reserved_bytes = estimated_bytes),

    maximum_bytes_billed BIGINT NOT NULL
        CHECK(
            maximum_bytes_billed >= 1
            AND maximum_bytes_billed <= 1073741824
        ),

    output_rows INTEGER NOT NULL
        CHECK(output_rows BETWEEN 1 AND 1000),

    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK(status IN ('ACTIVE','EXECUTING','COMPLETED','FAILED','RELEASED')),

    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    execution_started_at TIMESTAMPTZ,
    finalized_at TIMESTAMPTZ,

    CHECK(expires_at > created_at),

    CHECK(
        (
            status = 'ACTIVE'
            AND execution_started_at IS NULL
            AND finalized_at IS NULL
        )
        OR
        (
            status = 'EXECUTING'
            AND execution_started_at IS NOT NULL
            AND finalized_at IS NULL
        )
        OR
        (
            status = 'COMPLETED'
            AND execution_started_at IS NOT NULL
            AND finalized_at IS NOT NULL
        )
        OR
        (
            status = 'FAILED'
            AND execution_started_at IS NOT NULL
            AND finalized_at IS NOT NULL
        )
        OR
        (
            status = 'RELEASED'
            AND execution_started_at IS NULL
            AND finalized_at IS NOT NULL
        )
    ),

    UNIQUE(workspace_id, owner_key, reservation_id)
);

CREATE INDEX query_governance_active_workspace
    ON query_governance_reservations(
        workspace_id,
        status,
        expires_at
    );

CREATE INDEX query_governance_active_global
    ON query_governance_reservations(
        status,
        expires_at
    );


CREATE TABLE query_governance_usage (
    usage_id BIGSERIAL PRIMARY KEY,

    reservation_id BIGINT NOT NULL UNIQUE,
    workspace_id BIGINT NOT NULL,
    owner_key TEXT NOT NULL,

    outcome TEXT NOT NULL
        CHECK(outcome IN ('SUCCEEDED','FAILED')),

    accounting_basis TEXT NOT NULL
        CHECK(accounting_basis IN ('ACTUAL','RESERVED_FALLBACK')),

    bytes_processed BIGINT
        CHECK(bytes_processed IS NULL OR bytes_processed >= 0),

    bytes_billed BIGINT
        CHECK(bytes_billed IS NULL OR bytes_billed >= 0),

    accounted_bytes BIGINT NOT NULL
        CHECK(accounted_bytes >= 0),

    CHECK(
        (
            accounting_basis = 'ACTUAL'
            AND bytes_processed IS NOT NULL
            AND bytes_billed IS NOT NULL
            AND accounted_bytes = GREATEST(
                bytes_processed,
                bytes_billed
            )
        )
        OR
        (
            accounting_basis = 'RESERVED_FALLBACK'
            AND outcome = 'FAILED'
            AND (
                bytes_processed IS NULL
                OR bytes_billed IS NULL
            )
        )
    ),

    provider_job_id TEXT
        CHECK(
            provider_job_id IS NULL
            OR length(provider_job_id) BETWEEN 1 AND 255
        ),

    duration_ms BIGINT NOT NULL
        CHECK(duration_ms >= 0),

    accounted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    FOREIGN KEY(workspace_id, owner_key, reservation_id)
        REFERENCES query_governance_reservations(
            workspace_id,
            owner_key,
            reservation_id
        )
);

CREATE INDEX query_governance_usage_workspace_time
    ON query_governance_usage(
        workspace_id,
        accounted_at
    );

CREATE INDEX query_governance_usage_project_time
    ON query_governance_usage(accounted_at);


-- Workspace ownership must be current when a reservation is created.
-- Reservation identity/budget fields are immutable after creation.
CREATE FUNCTION query_governance_reservation_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM workspaces
        WHERE workspace_id = NEW.workspace_id
          AND owner = NEW.owner_key
    ) THEN
        RAISE EXCEPTION
            'Query reservation must belong to current workspace owner';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF ROW(
            NEW.request_token,
            NEW.intent_sha256,
            NEW.workspace_id,
            NEW.owner_key,
            NEW.consumer,
            NEW.provider,
            NEW.estimated_bytes,
            NEW.estimate_confidence,
            NEW.reserved_bytes,
            NEW.maximum_bytes_billed,
            NEW.output_rows,
            NEW.expires_at,
            NEW.created_at
        ) IS DISTINCT FROM ROW(
            OLD.request_token,
            OLD.intent_sha256,
            OLD.workspace_id,
            OLD.owner_key,
            OLD.consumer,
            OLD.provider,
            OLD.estimated_bytes,
            OLD.estimate_confidence,
            OLD.reserved_bytes,
            OLD.maximum_bytes_billed,
            OLD.output_rows,
            OLD.expires_at,
            OLD.created_at
        ) THEN
            RAISE EXCEPTION
                'Query reservation identity is immutable';
        END IF;

        IF NOT (
            (OLD.status = 'ACTIVE' AND NEW.status IN ('EXECUTING','RELEASED'))
            OR
            (OLD.status = 'EXECUTING' AND NEW.status IN ('COMPLETED','FAILED'))
        ) THEN
            RAISE EXCEPTION
                'Invalid query reservation transition';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER query_governance_reservation_validate
BEFORE INSERT OR UPDATE
ON query_governance_reservations
FOR EACH ROW
EXECUTE FUNCTION query_governance_reservation_guard();


-- Usage is append-only and must reference the exact ACTIVE reservation.
CREATE FUNCTION query_governance_usage_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    reservation_bytes BIGINT;
BEGIN
    IF TG_OP IN ('UPDATE','DELETE') THEN
        RAISE EXCEPTION
            'Query usage evidence is immutable';
    END IF;

    SELECT r.reserved_bytes
    INTO reservation_bytes
    FROM query_governance_reservations r
    WHERE r.reservation_id = NEW.reservation_id
      AND r.workspace_id = NEW.workspace_id
      AND r.owner_key = NEW.owner_key
      AND r.status = 'EXECUTING';

    IF reservation_bytes IS NULL THEN
        RAISE EXCEPTION
            'Executing matching query reservation required';
    END IF;

    IF NEW.accounting_basis = 'RESERVED_FALLBACK'
       AND NEW.accounted_bytes <> GREATEST(
            reservation_bytes,
            COALESCE(NEW.bytes_processed, 0),
            COALESCE(NEW.bytes_billed, 0)
       ) THEN
        RAISE EXCEPTION
            'Reserved fallback accounting mismatch';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER query_governance_usage_validate
BEFORE INSERT OR UPDATE OR DELETE
ON query_governance_usage
FOR EACH ROW
EXECUTE FUNCTION query_governance_usage_guard();


-- Reservation rows cannot be removed. Status transition preserves audit identity.
CREATE FUNCTION query_governance_reservation_no_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'Query reservations cannot be deleted';
END;
$$;

CREATE TRIGGER query_governance_reservation_delete_guard
BEFORE DELETE
ON query_governance_reservations
FOR EACH ROW
EXECUTE FUNCTION query_governance_reservation_no_delete();


CREATE FUNCTION query_governance_no_truncate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'Query governance history cannot be truncated';
END;
$$;

CREATE TRIGGER query_governance_reservation_truncate_guard
BEFORE TRUNCATE
ON query_governance_reservations
FOR EACH STATEMENT
EXECUTE FUNCTION query_governance_no_truncate();

CREATE TRIGGER query_governance_usage_truncate_guard
BEFORE TRUNCATE
ON query_governance_usage
FOR EACH STATEMENT
EXECUTE FUNCTION query_governance_no_truncate();


-- At commit:
-- COMPLETED requires exactly one usage row.
-- ACTIVE and RELEASED require zero usage rows.
CREATE FUNCTION query_governance_completion_consistent()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    rid BIGINT;
    state TEXT;
    usage_count BIGINT;
BEGIN
    rid := COALESCE(NEW.reservation_id, OLD.reservation_id);

    SELECT status
    INTO state
    FROM query_governance_reservations
    WHERE reservation_id = rid;

    IF state IS NULL THEN
        RETURN NULL;
    END IF;

    SELECT count(*)
    INTO usage_count
    FROM query_governance_usage
    WHERE reservation_id = rid;

    IF state IN ('COMPLETED','FAILED') AND usage_count <> 1 THEN
        RAISE EXCEPTION
            'Terminal query reservation requires one usage record';
    END IF;

    IF state IN ('ACTIVE','EXECUTING','RELEASED') AND usage_count <> 0 THEN
        RAISE EXCEPTION
            'Non-completed reservation cannot have usage';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER query_governance_reservation_consistency
AFTER INSERT OR UPDATE
ON query_governance_reservations
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION query_governance_completion_consistent();

CREATE CONSTRAINT TRIGGER query_governance_usage_consistency
AFTER INSERT OR UPDATE OR DELETE
ON query_governance_usage
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION query_governance_completion_consistent();

COMMIT;
