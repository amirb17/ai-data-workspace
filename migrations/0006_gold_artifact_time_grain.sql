-- =========================================================
-- Migration 0006: Gold artifact temporal grain
-- =========================================================
--
-- Adds temporal grain metadata to the Gold semantic model.
--
-- Examples:
--   order_date_day_summary   -> DAY
--   order_date_month_summary -> MONTH
--   order_date_year_summary  -> YEAR
--
-- Non-temporal marts keep time_grain as NULL.
-- =========================================================

ALTER TABLE gold_artifact_models
    ADD COLUMN IF NOT EXISTS time_grain VARCHAR;


ALTER TABLE gold_artifact_models
    DROP CONSTRAINT IF EXISTS chk_gold_artifact_models_time_grain;


ALTER TABLE gold_artifact_models
    ADD CONSTRAINT chk_gold_artifact_models_time_grain
    CHECK (
        time_grain IS NULL
        OR time_grain IN (
            'DAY',
            'MONTH',
            'YEAR'
        )
    );