-- =========================================================
-- Migration 0005: Gold semantic catalog
-- =========================================================
--
-- Stores machine-readable semantic metadata for published
-- Gold artifacts.
--
-- gold_artifact_models:
--     artifact-level analytical metadata such as grain.
--
-- gold_artifact_columns:
--     column-level semantic lineage including dimensions,
--     measures, metrics, source columns and aggregations.
-- =========================================================


CREATE TABLE IF NOT EXISTS gold_artifact_models (
    gold_artifact_model_id BIGSERIAL PRIMARY KEY,

    gold_artifact_id BIGINT NOT NULL UNIQUE,

    grain TEXT NOT NULL,

    created_at TIMESTAMP WITHOUT TIME ZONE
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_gold_artifact_models_artifact
        FOREIGN KEY (gold_artifact_id)
        REFERENCES gold_artifacts(gold_artifact_id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS gold_artifact_columns (
    gold_artifact_column_id BIGSERIAL PRIMARY KEY,

    gold_artifact_id BIGINT NOT NULL,

    column_name VARCHAR NOT NULL,

    column_role VARCHAR NOT NULL,

    source_column VARCHAR,

    aggregation_type VARCHAR,

    ordinal_position INTEGER NOT NULL,

    data_type VARCHAR,

    created_at TIMESTAMP WITHOUT TIME ZONE
        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_gold_artifact_columns_artifact
        FOREIGN KEY (gold_artifact_id)
        REFERENCES gold_artifacts(gold_artifact_id)
        ON DELETE CASCADE,

    CONSTRAINT uq_gold_artifact_column
        UNIQUE (
            gold_artifact_id,
            column_name
        ),

    CONSTRAINT chk_gold_artifact_column_role
        CHECK (
            column_role IN (
                'DIMENSION',
                'MEASURE',
                'METRIC'
            )
        ),

    CONSTRAINT chk_gold_artifact_ordinal_position
        CHECK (ordinal_position > 0)
);


CREATE INDEX IF NOT EXISTS
    idx_gold_artifact_columns_artifact
    ON gold_artifact_columns(gold_artifact_id);


CREATE INDEX IF NOT EXISTS
    idx_gold_artifact_columns_role
    ON gold_artifact_columns(column_role);