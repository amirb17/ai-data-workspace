-- Prepare the Gold control plane for Exact Decimal V2.
-- V2 SUCCESS publication remains disabled.

ALTER TABLE dataset_gold_runs
    DROP CONSTRAINT dataset_gold_runs_build_version_check;

ALTER TABLE dataset_gold_runs
    ADD CONSTRAINT dataset_gold_runs_build_version_check
    CHECK (build_version IN (1, 2));

ALTER TABLE dataset_gold_runs
    ADD COLUMN exact_gold_approval_id BIGINT
    REFERENCES exact_gold_avg_policies(approval_id);

ALTER TABLE dataset_gold_runs
    ADD CONSTRAINT dataset_gold_run_version_approval_check
    CHECK (
        (build_version = 1 AND exact_gold_approval_id IS NULL)
        OR
        (build_version = 2 AND exact_gold_approval_id IS NOT NULL)
    );


CREATE FUNCTION guard_exact_gold_v2_run()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- Never change the approval identity after a run starts.
    IF TG_OP = 'UPDATE'
       AND NEW.exact_gold_approval_id
           IS DISTINCT FROM OLD.exact_gold_approval_id
    THEN
        RAISE EXCEPTION
            'Gold run approval pin is immutable';
    END IF;

    -- Preserve existing V1 behavior.
    IF NEW.build_version = 1 THEN
        RETURN NEW;
    END IF;

    IF NEW.exact_gold_approval_id IS NULL THEN
        RAISE EXCEPTION
            'Pinned Exact Gold AVG approval required';
    END IF;

    -- Independently verify ownership and source pins.
    IF NOT EXISTS (
        SELECT 1
        FROM exact_gold_avg_policies p
        JOIN dataset_state_versions s
          ON s.state_id = p.source_state_id
        JOIN delivery_applications a
          ON a.application_id = s.source_application_id
        WHERE p.approval_id = NEW.exact_gold_approval_id
          AND p.source_state_id = NEW.source_state_id
          AND p.dataset_id = NEW.dataset_id
          AND p.dataset_version_id = NEW.dataset_version_id
          AND p.source_policy_id = NEW.policy_id
          AND p.applied_rule_version = a.applied_rule_version
          AND s.status = 'PUBLISHED'
          AND s.dataset_id = NEW.dataset_id
          AND s.dataset_version_id = NEW.dataset_version_id
          AND s.policy_id = NEW.policy_id
          AND s.manifest_sha256 = NEW.source_sha256
    ) THEN
        RAISE EXCEPTION
            'Exact Gold run approval/source pins differ';
    END IF;

    -- A subsequent migration will replace this guard
    -- after the authenticated V2 publisher is tested.
    IF NEW.status = 'SUCCESS' THEN
        RAISE EXCEPTION
            'Exact Gold V2 publication is not enabled';
    END IF;

    RETURN NEW;
END;
$$;


CREATE TRIGGER dataset_gold_v2_run_guard
BEFORE INSERT OR UPDATE
ON dataset_gold_runs
FOR EACH ROW
EXECUTE FUNCTION guard_exact_gold_v2_run();