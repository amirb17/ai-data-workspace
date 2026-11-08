
-- Task 21H.158E2 / E2.3C
-- Durable Gold verification references for Sydney warehouse intents.
--
-- This migration does not enable Sydney provider execution.
-- Existing development/Mumbai intents remain compatible.

BEGIN;

ALTER TABLE warehouse_execution_intents
ADD COLUMN gold_verification_id BIGINT;

ALTER TABLE warehouse_execution_intents
ADD COLUMN gold_source_fingerprint CHAR(64);

ALTER TABLE warehouse_execution_intents
ADD COLUMN gold_run_id BIGINT;

ALTER TABLE warehouse_execution_intents
ADD CONSTRAINT warehouse_intent_gold_verification_fk
FOREIGN KEY (gold_verification_id)
REFERENCES dataset_gold_evidence(verification_id)
ON UPDATE RESTRICT
ON DELETE RESTRICT;

ALTER TABLE warehouse_execution_intents
ADD CONSTRAINT warehouse_intent_gold_run_fk
FOREIGN KEY (gold_run_id)
REFERENCES dataset_gold_runs(gold_run_id)
ON UPDATE RESTRICT
ON DELETE RESTRICT;

ALTER TABLE warehouse_execution_intents
ADD CONSTRAINT warehouse_intent_gold_pin_consistency
CHECK (
    (
        gold_verification_id IS NULL
        AND gold_source_fingerprint IS NULL
        AND gold_run_id IS NULL
    )
    OR
    (
        gold_verification_id IS NOT NULL
        AND gold_source_fingerprint IS NOT NULL
        AND gold_run_id IS NOT NULL
        AND gold_source_fingerprint ~ '^[a-f0-9]{64}$'
    )
);

-- Production Sydney intents require complete Gold pins.
-- Existing development and Mumbai resources remain compatible.

CREATE FUNCTION warehouse_sydney_intent_gold_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    resource warehouse_resources%ROWTYPE;
    evidence dataset_gold_evidence%ROWTYPE;
BEGIN
    SELECT *
    INTO resource
    FROM warehouse_resources
    WHERE resource_id = NEW.resource_id
      AND workspace_id = NEW.workspace_id
      AND warehouse_model_id = NEW.warehouse_model_id
      AND warehouse_model_version_id =
          NEW.warehouse_model_version_id
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Registered warehouse resource required';
    END IF;

    IF resource.environment = 'production'
       AND resource.location = 'australia-southeast1'
    THEN
        IF NEW.gold_verification_id IS NULL
           OR NEW.gold_source_fingerprint IS NULL
           OR NEW.gold_run_id IS NULL
        THEN
            RAISE EXCEPTION
                'Sydney warehouse intent requires verified Gold pins';
        END IF;


        SELECT e.*
        INTO evidence
        FROM dataset_gold_evidence e
        JOIN dataset_gold_runs g
          ON g.gold_run_id = e.gold_run_id
        JOIN warehouse_model_sources s
          ON s.workspace_id = NEW.workspace_id
         AND s.warehouse_model_id = NEW.warehouse_model_id
         AND s.warehouse_model_version_id =
             NEW.warehouse_model_version_id
         AND s.source_dataset_id = g.dataset_id
        JOIN warehouse_model_configurations c
          ON c.warehouse_model_version_id =
             NEW.warehouse_model_version_id
         AND c.source_dataset_id = g.dataset_id
         AND c.source_dataset_version_id =
             g.dataset_version_id
         AND c.source_policy_id = g.policy_id
        WHERE e.verification_id = NEW.gold_verification_id
          AND e.gold_run_id = NEW.gold_run_id
          AND e.source_fingerprint =
              NEW.gold_source_fingerprint
          AND g.status = 'SUCCESS'
        FOR SHARE OF e, g, s, c;


        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Warehouse Gold verification pins mismatch';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER warehouse_sydney_intent_gold_insert
BEFORE INSERT ON warehouse_execution_intents
FOR EACH ROW
EXECUTE FUNCTION warehouse_sydney_intent_gold_guard();

COMMIT;
