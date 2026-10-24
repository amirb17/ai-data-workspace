-- Immutable evidence that one PostgreSQL publication has been
-- materialized and independently reconciled into the registered
-- BigQuery serving table.
--
-- Additive metadata only. This migration performs no BigQuery work
-- and does not make existing publications queryable automatically.

BEGIN;

CREATE TABLE warehouse_serving_materializations (
    serving_materialization_id BIGSERIAL PRIMARY KEY,

    workspace_id BIGINT NOT NULL,
    warehouse_model_id BIGINT NOT NULL,
    warehouse_model_version_id BIGINT NOT NULL,

    resource_id BIGINT NOT NULL,
    publication_id BIGINT NOT NULL,
    publication_revision BIGINT NOT NULL
        CHECK(publication_revision > 0),

    generation INTEGER NOT NULL
        CHECK(generation > 0),

    materialization_token TEXT NOT NULL UNIQUE
        CHECK(materialization_token ~ '^[a-f0-9]{64}$'),

    intent_sha256 TEXT NOT NULL
        CHECK(intent_sha256 ~ '^[a-f0-9]{64}$'),

    configuration_sha256 TEXT NOT NULL
        CHECK(configuration_sha256 ~ '^[a-f0-9]{64}$'),

    schema_sha256 TEXT NOT NULL
        CHECK(schema_sha256 ~ '^[a-f0-9]{64}$'),

    source_vector_sha256 TEXT NOT NULL
        CHECK(source_vector_sha256 ~ '^[a-f0-9]{64}$'),

    provider_job_id TEXT NOT NULL UNIQUE
        CHECK(length(provider_job_id) BETWEEN 1 AND 255),

    serving_sha256 TEXT NOT NULL
        CHECK(serving_sha256 ~ '^[a-f0-9]{64}$'),

    row_count BIGINT NOT NULL
        CHECK(row_count >= 0),

    bytes_processed BIGINT NOT NULL
        CHECK(bytes_processed >= 0),

    bytes_billed BIGINT NOT NULL
        CHECK(bytes_billed >= 0),

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT clock_timestamp(),

    FOREIGN KEY(
        workspace_id,
        warehouse_model_id,
        warehouse_model_version_id
    )
        REFERENCES warehouse_model_versions(
            workspace_id,
            warehouse_model_id,
            warehouse_model_version_id
        ),

    FOREIGN KEY(
        resource_id,
        publication_id
    )
        REFERENCES warehouse_publications(
            resource_id,
            publication_id
        ),

    UNIQUE(
        resource_id,
        publication_id
    ),

    UNIQUE(
        resource_id,
        publication_revision
    )
);


CREATE FUNCTION warehouse_serving_materialization_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    publication warehouse_publications;
    intent warehouse_execution_intents;
    resource warehouse_resources;
    head warehouse_publication_heads;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION
            'Serving materialization evidence is immutable';
    END IF;

    SELECT *
    INTO publication
    FROM warehouse_publications
    WHERE publication_id = NEW.publication_id
      AND resource_id = NEW.resource_id;

    IF publication.publication_id IS NULL THEN
        RAISE EXCEPTION
            'Published warehouse evidence required';
    END IF;

    SELECT *
    INTO intent
    FROM warehouse_execution_intents
    WHERE operation_id =
          publication.operation_id;

    SELECT *
    INTO resource
    FROM warehouse_resources
    WHERE resource_id =
          NEW.resource_id;

    SELECT *
    INTO head
    FROM warehouse_publication_heads
    WHERE resource_id =
          NEW.resource_id;

    IF intent.operation_id IS NULL
       OR resource.resource_id IS NULL
       OR head.resource_id IS NULL
    THEN
        RAISE EXCEPTION
            'Complete warehouse publication scope required';
    END IF;

    IF ROW(
        intent.workspace_id,
        intent.warehouse_model_id,
        intent.warehouse_model_version_id,
        intent.resource_id,
        intent.generation
    ) IS DISTINCT FROM ROW(
        NEW.workspace_id,
        NEW.warehouse_model_id,
        NEW.warehouse_model_version_id,
        NEW.resource_id,
        NEW.generation
    )
    THEN
        RAISE EXCEPTION
            'Serving materialization scope differs from publication';
    END IF;

    IF publication.revision
           IS DISTINCT FROM
           NEW.publication_revision
       OR head.publication_id
           IS DISTINCT FROM
           NEW.publication_id
       OR head.revision
           IS DISTINCT FROM
           NEW.publication_revision
    THEN
        RAISE EXCEPTION
            'Current publication head required for serving materialization';
    END IF;

    IF resource.workspace_id
           IS DISTINCT FROM
           NEW.workspace_id
       OR resource.warehouse_model_id
           IS DISTINCT FROM
           NEW.warehouse_model_id
       OR resource.warehouse_model_version_id
           IS DISTINCT FROM
           NEW.warehouse_model_version_id
       OR resource.generation
           IS DISTINCT FROM
           NEW.generation
       OR resource.provider
           IS DISTINCT FROM
           'BIGQUERY'
       OR resource.status
           IS DISTINCT FROM
           'REGISTERED'
    THEN
        RAISE EXCEPTION
            'Registered BigQuery resource required';
    END IF;

    RETURN NEW;
END;
$$;


CREATE TRIGGER warehouse_serving_materialization_validate
BEFORE INSERT OR UPDATE OR DELETE
ON warehouse_serving_materializations
FOR EACH ROW
EXECUTE FUNCTION warehouse_serving_materialization_guard();


CREATE TRIGGER warehouse_serving_materialization_no_truncate
BEFORE TRUNCATE
ON warehouse_serving_materializations
FOR EACH STATEMENT
EXECUTE FUNCTION warehouse_no_truncate();

COMMIT;